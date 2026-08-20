"""Headless FBX import, UV processing, export, and still-render helpers."""

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Sequence


MULTI_ANGLE_VIEWS = ("front", "back", "left", "right", "top", "bottom", "perspective")
DEFAULT_MULTI_ANGLE_VIEWS = ("front", "right", "top", "perspective")

SMART_UV_MARGIN_METHODS = ("SCALED", "ADD", "FRACTION")
SMART_UV_ROTATE_METHODS = ("AXIS_ALIGNED", "AXIS_ALIGNED_X", "AXIS_ALIGNED_Y")
AUTO_UV_ALGORITHMS = ("autouv", "uniform")
SMART_UV_DEFAULTS = {
    "angle_limit": 1.1519173383712769,
    "margin_method": "SCALED",
    "rotate_method": "AXIS_ALIGNED_Y",
    "island_margin": 0.0,
    "area_weight": 0.0,
    "correct_aspect": True,
    "scale_to_bounds": False,
}
UNIFORM_UV_DEFAULT_ANGLE_DEGREES = (10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 66.0)
MINISTRY_OF_FLAT_DEFAULTS = {
    "resolution": 1024,
    "separate_hard_edges": False,
    "aspect": 1.0,
    "use_normals": False,
    "udims": 1,
    "overlap_identical": False,
    "overlap_mirrored": False,
    "world_scale": False,
    "density": 1024,
    # AutoUV pipeline options.  They are kept alongside the external options
    # so the generated Blender script has one validated configuration object.
    "merge_meshes": True,
    "normalize_uv": True,
}
AUTO_UV_SINGLE_TILE_MARGIN = 0.001
TOPOLOGY_PREFILTER_DEFAULT = True
TOPOLOGY_PREFILTER_LEVELS = ("off", "high", "medium")
TOPOLOGY_RISK_VERSION = 2
TOPOLOGY_RISK_THRESHOLD = 7
AUTO_UV_FILE_TIMEOUT = 300


def _topology_risk_level(score: int) -> str:
    if score >= TOPOLOGY_RISK_THRESHOLD:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _validate_topology_prefilter_level(level: Optional[str]) -> str:
    value = str(level or "").strip().lower()
    if value not in TOPOLOGY_PREFILTER_LEVELS:
        choices = ", ".join(TOPOLOGY_PREFILTER_LEVELS)
        raise ValueError(
            f"Unknown topology prefilter level {level!r}; choose one of: {choices}"
        )
    return value


def _resolve_topology_prefilter_level(
    level: Optional[str],
    legacy_enabled: Optional[bool],
) -> str:
    """Resolve the new level API while preserving the old boolean API."""
    if level is not None:
        normalized = _validate_topology_prefilter_level(level)
        if legacy_enabled is not None:
            raise ValueError(
                "topology_prefilter_level conflicts with topology_prefilter; "
                "use only one topology prefilter option"
            )
        return normalized
    if legacy_enabled is None:
        return "high"
    return "high" if legacy_enabled else "off"


def _score_topology_metrics(metrics: Dict[str, object]) -> Dict[str, object]:
    """Score imported mesh metrics using the versioned AutoUV preflight rules."""
    vertices = int(metrics.get("vertices", 0) or 0)
    polygons = int(metrics.get("polygons", 0) or 0)
    loops = int(metrics.get("loops", 0) or 0)
    ngons = int(metrics.get("ngons", 0) or 0)
    max_polygon_vertices = int(metrics.get("max_polygon_vertices", 0) or 0)
    boundary_edges = int(metrics.get("boundary_edges", 0) or 0)
    edge_count = max(int(metrics.get("edges", 0) or 0), 1)
    duplicate_groups = int(metrics.get("duplicate_position_groups", 0) or 0)
    zero_area_faces = int(metrics.get("zero_area_faces", 0) or 0)
    interior_non_manifold_edges = int(metrics.get("interior_non_manifold_edges", 0) or 0)
    boundary_edge_ratio = float(metrics.get("boundary_edge_ratio", boundary_edges / edge_count) or 0.0)
    ngon_ratio = float(metrics.get("ngon_ratio", ngons / max(polygons, 1)) or 0.0)

    triggered = []

    def add_rule(code: str, reason: str, value: object, threshold: object, points: int) -> None:
        triggered.append({
            "code": code,
            "reason": reason,
            "value": value,
            "threshold": threshold,
            "points": points,
        })

    if vertices >= 3500:
        add_rule("vertices_high", "vertices >= 3500", vertices, 3500, 3)
    if polygons >= 3000:
        add_rule("polygons_high", "polygons >= 3000", polygons, 3000, 2)
    if loops >= 12000:
        add_rule("loops_high", "loops >= 12000", loops, 12000, 2)

    if boundary_edge_ratio >= 0.22:
        add_rule("boundary_ratio_very_high", "boundary_edge_ratio >= 0.22", boundary_edge_ratio, 0.22, 3)
    elif boundary_edge_ratio >= 0.18:
        add_rule("boundary_ratio_high", "boundary_edge_ratio >= 0.18", boundary_edge_ratio, 0.18, 2)
    if boundary_edges >= 200:
        add_rule("boundary_edges_high", "boundary_edges >= 200", boundary_edges, 200, 1)

    if ngon_ratio >= 0.20:
        add_rule("ngon_ratio_very_high", "ngon_ratio >= 0.20", ngon_ratio, 0.20, 3)
    elif ngon_ratio >= 0.10:
        add_rule("ngon_ratio_high", "ngon_ratio >= 0.10", ngon_ratio, 0.10, 2)

    if max_polygon_vertices >= 13:
        add_rule("max_polygon_vertices_very_high", "max_polygon_vertices >= 13", max_polygon_vertices, 13, 3)
    elif max_polygon_vertices >= 7:
        add_rule("max_polygon_vertices_high", "max_polygon_vertices >= 7", max_polygon_vertices, 7, 2)
    elif max_polygon_vertices >= 5:
        add_rule("max_polygon_vertices_elevated", "max_polygon_vertices >= 5", max_polygon_vertices, 5, 1)

    if duplicate_groups >= 3:
        add_rule("duplicate_positions", "duplicate_position_groups >= 3", duplicate_groups, 3, 1)
    if zero_area_faces > 0:
        add_rule("zero_area_faces", "zero_area_faces > 0", zero_area_faces, 0, 4)
    if interior_non_manifold_edges >= 50:
        add_rule(
            "interior_non_manifold_edges",
            "interior_non_manifold_edges >= 50",
            interior_non_manifold_edges,
            50,
            2,
        )

    if boundary_edge_ratio >= 0.18 and ngons >= 20:
        add_rule(
            "open_boundary_ngon_combo",
            "boundary_edge_ratio >= 0.18 and ngons >= 20",
            {"boundary_edge_ratio": boundary_edge_ratio, "ngons": ngons},
            {"boundary_edge_ratio": 0.18, "ngons": 20},
            2,
        )
    if polygons < 1000 and boundary_edge_ratio >= 0.18 and ngons >= 20:
        add_rule(
            "small_open_complex_mesh",
            "polygons < 1000 and boundary_edge_ratio >= 0.18 and ngons >= 20",
            {"polygons": polygons, "boundary_edge_ratio": boundary_edge_ratio, "ngons": ngons},
            {"polygons": 1000, "boundary_edge_ratio": 0.18, "ngons": 20},
            2,
        )

    score = sum(int(rule["points"]) for rule in triggered)
    return {
        "risk_version": TOPOLOGY_RISK_VERSION,
        "risk_score": score,
        "risk_level": _topology_risk_level(score),
        "ngon_ratio": ngon_ratio,
        "interior_non_manifold_edges": interior_non_manifold_edges,
        "reasons": [rule["reason"] for rule in triggered],
        "triggered_rules": triggered,
    }


def _validate_ministry_of_flat_options(options: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Validate the documented, user-facing Ministry of Flat options."""
    values = dict(MINISTRY_OF_FLAT_DEFAULTS)
    incoming = options or {}
    unknown = set(incoming).difference(values)
    if unknown:
        raise ValueError(f"Unknown AutoUV option(s): {', '.join(sorted(unknown))}")
    values.update({key: value for key, value in incoming.items() if value is not None})

    values["resolution"] = int(values["resolution"])
    values["udims"] = int(values["udims"])
    values["density"] = int(values["density"])
    values["aspect"] = float(values["aspect"])
    if values["resolution"] < 1:
        raise ValueError("AutoUV resolution must be positive.")
    if values["udims"] < 1:
        raise ValueError("AutoUV UDIM count must be positive.")
    if values["density"] < 1:
        raise ValueError("AutoUV density must be positive.")
    if not math.isfinite(values["aspect"]) or values["aspect"] <= 0.0:
        raise ValueError("AutoUV aspect must be finite and positive.")
    for key in (
        "separate_hard_edges",
        "use_normals",
        "overlap_identical",
        "overlap_mirrored",
        "world_scale",
        "merge_meshes",
        "normalize_uv",
    ):
        values[key] = bool(values[key])
    return values


def resolve_ministry_of_flat_executable(executable_path: Optional[str] = None) -> str:
    """Resolve the bundled or explicitly configured Ministry of Flat console EXE."""
    candidates = []
    if executable_path:
        candidates.append(Path(os.path.abspath(os.path.expanduser(executable_path))))
    configured = os.environ.get("MINISTRY_OF_FLAT_EXE")
    if configured:
        candidates.append(Path(os.path.abspath(os.path.expanduser(configured))))

    # Keep the binary inside the installed ``cli_anything.blender`` package so
    # an installed Harness does not depend on the repository's tools folder.
    candidates.append(
        Path(__file__).resolve().parents[1]
        / "third_party"
        / "MinistryOfFlat"
        / "UnWrapConsole3.exe"
    )

    # Source-tree fallback for older checkouts and the Blender toolbox bundle.
    package_path = Path(__file__).resolve()
    for parent in package_path.parents:
        candidates.append(
            parent / "tools" / "modeling_toolbox" / "third_party" / "MinistryOfFlat" / "UnWrapConsole3.exe"
        )
    candidates.append(
        Path.cwd()
        / "tools"
        / "modeling_toolbox"
        / "third_party"
        / "MinistryOfFlat"
        / "UnWrapConsole3.exe"
    )

    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(str(candidate)))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file():
            return str(candidate.resolve())
    if executable_path:
        raise FileNotFoundError(f"Ministry of Flat executable not found: {executable_path}")
    raise FileNotFoundError(
        "UnWrapConsole3.exe not found. Use --unwrap-exe or set MINISTRY_OF_FLAT_EXE."
    )


def render_fbx(fbx_path: str, output_path: str, **options) -> Dict[str, object]:
    """Import an FBX into an empty Blender scene and render one still image."""
    fbx_path = os.path.abspath(fbx_path)
    output_path = os.path.abspath(output_path)
    if not os.path.isfile(fbx_path):
        raise FileNotFoundError(f"FBX file not found: {fbx_path}")
    if os.path.splitext(fbx_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx file: {fbx_path}")
    return _render(fbx_path, output_path, **options)


def _render(fbx_path: str, output_path: str, *, color_by_material: bool = False,
            engine: str = "EEVEE", resolution_x: int = 1920,
            resolution_y: int = 1080, samples: int = 64,
            transparent: bool = False, overwrite: bool = False,
            timeout: int = 300) -> Dict[str, object]:
    """Validate render options, run Blender headlessly, and verify output."""
    if os.path.exists(output_path) and not overwrite:
        raise FileExistsError(f"Output file exists: {output_path}. Use --overwrite.")
    if resolution_x < 1 or resolution_y < 1 or samples < 1:
        raise ValueError("Render resolution and samples must be positive.")
    formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP",
               ".tif": "TIFF", ".tiff": "TIFF", ".exr": "OPEN_EXR"}
    output_format = formats.get(os.path.splitext(output_path)[1].lower())
    if output_format is None:
        raise ValueError("Output must use .png, .jpg/.jpeg, .bmp, .tif/.tiff, or .exr.")
    if engine not in {"CYCLES", "EEVEE", "WORKBENCH"}:
        raise ValueError("Engine must be CYCLES, EEVEE, or WORKBENCH.")
    from cli_anything.blender.utils.blender_backend import render_scene_headless
    result = render_scene_headless(generate_fbx_render_script(
        fbx_path, output_path, color_by_material=color_by_material, engine=engine,
        resolution_x=resolution_x, resolution_y=resolution_y, samples=samples,
        transparent=transparent, output_format=output_format), output_path, timeout=timeout)
    result.update({"source_fbx": fbx_path, "color_by_material": color_by_material,
                   "engine": engine, "resolution": f"{resolution_x}x{resolution_y}"})
    return result


def render_fbx_multi_angle(fbx_path: str, output_dir: str, *, views: Optional[Iterable[str]] = None,
                           color_by_material: bool = False, extension: str = ".png",
                           engine: str = "EEVEE", resolution_x: int = 1920,
                           resolution_y: int = 1080, samples: int = 64,
                           transparent: bool = False, overwrite: bool = False,
                           timeout: int = 300) -> Dict[str, object]:
    """Import one FBX once and render several automatically framed viewpoints."""
    fbx_path = os.path.abspath(fbx_path)
    if not os.path.isfile(fbx_path):
        raise FileNotFoundError(f"FBX file not found: {fbx_path}")
    if os.path.splitext(fbx_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx file: {fbx_path}")
    selected_views = tuple(views or DEFAULT_MULTI_ANGLE_VIEWS)
    unknown_views = set(selected_views).difference(MULTI_ANGLE_VIEWS)
    if unknown_views:
        raise ValueError(f"Unknown views: {', '.join(sorted(unknown_views))}")
    if len(set(selected_views)) != len(selected_views):
        raise ValueError("Each view may be requested only once.")
    extension = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP",
               ".tif": "TIFF", ".tiff": "TIFF", ".exr": "OPEN_EXR"}
    output_format = formats.get(extension)
    if output_format is None:
        raise ValueError("Output format must be png, jpg, jpeg, bmp, tif, tiff, or exr.")
    if resolution_x < 1 or resolution_y < 1 or samples < 1:
        raise ValueError("Render resolution and samples must be positive.")
    if engine not in {"CYCLES", "EEVEE", "WORKBENCH"}:
        raise ValueError("Engine must be CYCLES, EEVEE, or WORKBENCH.")

    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    output_paths = {view: os.path.join(output_dir, f"{view}{extension}") for view in selected_views}
    existing = [path for path in output_paths.values() if os.path.exists(path)]
    if existing and not overwrite:
        raise FileExistsError(f"Output file exists: {existing[0]}. Use --overwrite.")

    from cli_anything.blender.utils.blender_backend import render_scene_headless
    script = generate_fbx_render_script(
        fbx_path, output_paths[selected_views[0]], color_by_material=color_by_material,
        engine=engine, resolution_x=resolution_x, resolution_y=resolution_y, samples=samples,
        transparent=transparent, output_format=output_format, output_paths=output_paths,
    )
    render_scene_headless(script, output_paths[selected_views[0]], timeout=timeout)
    missing = [path for path in output_paths.values() if not os.path.isfile(path)]
    if missing:
        raise RuntimeError(f"Blender produced no output for: {missing[0]}")
    outputs = [{"view": view, "path": path, "file_size": os.path.getsize(path)}
               for view, path in output_paths.items()]
    return {"source_fbx": fbx_path, "output_dir": output_dir, "outputs": outputs,
            "color_by_material": color_by_material, "engine": engine,
            "resolution": f"{resolution_x}x{resolution_y}"}


def generate_fbx_render_script(fbx_path: str, output_path: str, *,
                               color_by_material: bool = False, engine: str = "EEVEE",
                               resolution_x: int = 1920, resolution_y: int = 1080,
                               samples: int = 64, transparent: bool = False,
                               output_format: str = "PNG",
                               output_paths: Optional[Dict[str, str]] = None) -> str:
    """Generate the standalone bpy script used by :func:`render_fbx`."""
    config = repr({"fbx_path": fbx_path, "output_path": output_path,
                   "color_by_material": color_by_material, "engine": engine,
                   "resolution_x": resolution_x, "resolution_y": resolution_y,
                   "samples": samples, "transparent": transparent,
                   "output_format": output_format, "output_paths": output_paths})
    script = "\n".join([
        "import bpy", "import os", "from mathutils import Vector", f"CONFIG = {config}",
        "PALETTE = [(0.91, .18, .24, 1), (.10, .52, .93, 1), (.10, .72, .36, 1), (.96, .60, .12, 1), (.60, .28, .82, 1), (.06, .72, .72, 1), (.92, .30, .60, 1), (.62, .75, .16, 1)]",
        "def look_at(obj, target): obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()",
        "bpy.ops.object.select_all(action='SELECT')", "bpy.ops.object.delete(use_global=False)",
        "bpy.ops.import_scene.fbx(filepath=CONFIG['fbx_path'])",
        "meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH' and not obj.hide_render]",
        "if not meshes: raise RuntimeError('FBX import produced no mesh objects to render.')",
        "depsgraph = bpy.context.evaluated_depsgraph_get()", "corners = []",
        "for obj in meshes:",
        "    evaluated = obj.evaluated_get(depsgraph)",
        "    corners.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)",
        "minimum = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))",
        "maximum = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))",
        "center = (minimum + maximum) * .5", "diameter = max((maximum - minimum).length, .01)",
        "def default_gray_material():",
        "    material = bpy.data.materials.get('CLI_DefaultGray') or bpy.data.materials.new('CLI_DefaultGray')",
        "    material.use_nodes = True", "    material.diffuse_color = (.32, .32, .32, 1)",
        "    bsdf = material.node_tree.nodes.get('Principled BSDF')",
        "    if bsdf: bsdf.inputs['Base Color'].default_value = (.32, .32, .32, 1); bsdf.inputs['Roughness'].default_value = .55",
        "    return material",
        "def has_missing_image(material):",
        "    if material is None: return True",
        "    if not material.use_nodes: return False",
        "    for node in material.node_tree.nodes:",
        "        if node.type == 'TEX_IMAGE':",
        "            if node.image is None: return True",
        "            if not node.image.packed_file and node.image.filepath and not os.path.exists(bpy.path.abspath(node.image.filepath)): return True",
        "    return False",
        "fallback_gray = default_gray_material()",
        "for obj in meshes:",
        "    if not obj.data.materials: obj.data.materials.append(fallback_gray)",
        "    for slot_index, material in enumerate(obj.data.materials):",
        "        if has_missing_image(material): obj.data.materials[slot_index] = fallback_gray",
        "if CONFIG['color_by_material']:", "    color_index = 0",
        "    for obj in meshes:", "        if not obj.data.materials: obj.data.materials.append(None)",
        "        for slot_index in range(len(obj.data.materials)):",
        "            material = bpy.data.materials.new(f'CLI_MaterialColor_{color_index + 1:03d}')",
        "            material.use_nodes = True", "            bsdf = material.node_tree.nodes.get('Principled BSDF')",
        "            bsdf.inputs['Base Color'].default_value = PALETTE[color_index % len(PALETTE)]",
        "            material.diffuse_color = PALETTE[color_index % len(PALETTE)]",
        "            bsdf.inputs['Roughness'].default_value = .42", "            obj.data.materials[slot_index] = material",
        "            color_index += 1",
        "scene = bpy.context.scene",
        "scene.render.engine = {'CYCLES': 'CYCLES', 'EEVEE': 'BLENDER_EEVEE', 'WORKBENCH': 'BLENDER_WORKBENCH'}[CONFIG['engine']]",
        "scene.render.resolution_x = CONFIG['resolution_x']", "scene.render.resolution_y = CONFIG['resolution_y']",
        "scene.render.resolution_percentage = 100", "scene.render.film_transparent = CONFIG['transparent']",
        "scene.render.image_settings.file_format = CONFIG['output_format']", "scene.render.filepath = CONFIG['output_path']",
        "if CONFIG['engine'] == 'CYCLES': scene.cycles.samples = CONFIG['samples']",
        "if CONFIG['color_by_material']: scene.display.shading.color_type = 'MATERIAL'",
        "try: scene.view_settings.look = 'AgX - Medium High Contrast'",
        "except TypeError: pass",
        "world = bpy.data.worlds.new('CLI_World') if scene.world is None else scene.world", "scene.world = world",
        "world.use_nodes = True", "world.node_tree.nodes['Background'].inputs['Color'].default_value = (.035, .035, .035, 1)",
        "world.node_tree.nodes['Background'].inputs['Strength'].default_value = .08",
        "camera_data = bpy.data.cameras.new('CLI_Camera')", "camera = bpy.data.objects.new('CLI_Camera', camera_data)",
        "bpy.context.collection.objects.link(camera)",
        "camera.location = center + Vector((diameter * 1.15, -diameter * 1.15, diameter * .80))",
        "camera_data.lens = 50", "camera_data.clip_end = max(1000, diameter * 100)", "look_at(camera, center)", "scene.camera = camera",
        "for name, offset, energy, size in [('CLI_Key', (1.8, -1.6, 2.4), 700, 2.5), ('CLI_Fill', (-1.7, -.6, 1.2), 180, 3.5), ('CLI_Rim', (.4, 1.8, 2), 500, 2.0)]:",
        "    data = bpy.data.lights.new(name, type='AREA')", "    data.energy = energy * max(diameter, .5)",
        "    data.shape = 'DISK'", "    data.size = size * max(diameter, .5)",
        "    light = bpy.data.objects.new(name, data)", "    bpy.context.collection.objects.link(light)",
        "    light.location = center + Vector(offset) * diameter", "    look_at(light, center)",
        "bpy.ops.render.render(write_still=True)", "print('FBX render complete: ' + CONFIG['output_path'])",
    ])
    if not output_paths:
        return script
    view_offsets = {
        "front": (0.0, -2.6, 0.0), "back": (0.0, 2.6, 0.0),
        "left": (-2.6, 0.0, 0.0), "right": (2.6, 0.0, 0.0),
        "top": (0.0, 0.0, 2.6), "bottom": (0.0, 0.0, -2.6),
        "perspective": (1.8, -1.8, 1.25),
    }
    render_loop = "\n".join([
        f"VIEW_OFFSETS = {view_offsets!r}",
        "for view_name, output_path in CONFIG['output_paths'].items():",
        "    camera.location = center + Vector(VIEW_OFFSETS[view_name]) * diameter",
        "    look_at(camera, center)",
        "    scene.render.filepath = output_path",
        "    bpy.ops.render.render(write_still=True)",
        "    print('FBX render complete [' + view_name + ']: ' + output_path)",
    ])
    return script.replace(
        "bpy.ops.render.render(write_still=True)\nprint('FBX render complete: ' + CONFIG['output_path'])",
        render_loop,
    )


def _validate_smart_uv_options(options: Dict[str, object]) -> Dict[str, object]:
    """Validate and fill Smart UV Project options for the bpy script."""
    unknown = set(options).difference(SMART_UV_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown Smart UV option(s): {', '.join(sorted(unknown))}")
    values = dict(SMART_UV_DEFAULTS)
    values.update({key: value for key, value in options.items() if value is not None})

    if not 0.0 <= float(values["angle_limit"]) <= 3.141592653589793:
        raise ValueError("angle_limit must be between 0 and pi radians.")
    if values["margin_method"] not in SMART_UV_MARGIN_METHODS:
        raise ValueError(f"Unknown margin_method: {values['margin_method']}")
    if values["rotate_method"] not in SMART_UV_ROTATE_METHODS:
        raise ValueError(f"Unknown rotate_method: {values['rotate_method']}")
    if float(values["island_margin"]) < 0.0:
        raise ValueError("island_margin must be non-negative.")
    if not 0.0 <= float(values["area_weight"]) <= 1.0:
        raise ValueError("area_weight must be between 0 and 1.")
    values["angle_limit"] = float(values["angle_limit"])
    values["island_margin"] = float(values["island_margin"])
    values["area_weight"] = float(values["area_weight"])
    values["correct_aspect"] = bool(values["correct_aspect"])
    values["scale_to_bounds"] = bool(values["scale_to_bounds"])
    return values


def _validate_uniform_uv_angles(angle_candidates: Optional[Sequence[float]]) -> tuple[float, ...]:
    """Validate and normalize the angle candidates used by auto-unwrapping."""
    if angle_candidates is None:
        values = [math.radians(value) for value in UNIFORM_UV_DEFAULT_ANGLE_DEGREES]
    else:
        values = [float(value) for value in angle_candidates]
    if not values:
        raise ValueError("At least one angle candidate is required.")
    if any(not math.isfinite(value) or not 0.0 < value <= math.pi for value in values):
        raise ValueError("Each angle candidate must be finite and between 0 and pi radians.")
    return tuple(sorted(set(values)))


def _suffix_output_path(fbx_path: str, suffix: str) -> str:
    suffix = str(suffix)
    if not suffix:
        raise ValueError("Output suffix must not be empty.")
    if "/" in suffix or "\\" in suffix:
        raise ValueError("Output suffix must be a filename suffix, not a path.")
    directory, filename = os.path.split(fbx_path)
    stem, extension = os.path.splitext(filename)
    return os.path.join(directory, f"{stem}{suffix}{extension}")


def _uniform_uv_output_path(fbx_path: str) -> str:
    """Return the legacy sibling output name used by older callers."""
    return _suffix_output_path(fbx_path, "_uniform_uv")


def _smart_uv_output_path(fbx_path: str) -> str:
    directory, filename = os.path.split(fbx_path)
    stem, extension = os.path.splitext(filename)
    return os.path.join(directory, f"{stem}_uv{extension}")


def _same_path(first: str, second: str) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def _parse_script_marker(stdout: str, marker: str) -> Dict[str, object]:
    prefix = marker + "="
    for line in reversed(stdout.splitlines()):
        if line.startswith(prefix):
            return json.loads(line[len(prefix):])
    raise RuntimeError(f"Blender script did not emit {marker}.")


def _run_blender_script(
    script: str,
    timeout: int,
    operation: str,
    *,
    cancellation_context=None,
    input_fbx: Optional[str] = None,
) -> Dict[str, object]:
    from cli_anything.blender.utils.blender_backend import run_blender_script

    result = run_blender_script(
        script,
        timeout=timeout,
        cancellation=cancellation_context,
        input_fbx=input_fbx,
        operation=operation,
    )
    stdout = str(result.get("stdout", "") or "")
    stderr = str(result.get("stderr", "") or "")
    if result["returncode"] != 0:
        details = stderr or stdout
        raise RuntimeError(
            f"Blender {operation} failed (exit {result['returncode']}):\n{details[-2000:]}"
        )
    # Blender 5.x can print an unhandled Python exception while still
    # returning process exit code 0.  Do not mask that exception later as
    # the much less useful "produced no output" error.
    if "Traceback (most recent call last):" in stdout or "Traceback (most recent call last):" in stderr:
        details = stderr or stdout
        raise RuntimeError(
            f"Blender {operation} reported a script error:\n{details[-3000:]}"
        )
    return result


def export_fbx_smart_uv(
    fbx_path: str,
    output_path: Optional[str] = None,
    *,
    overwrite: bool = False,
    overwrite_source: bool = False,
    timeout: int = 300,
    **uv_options,
) -> Dict[str, object]:
    """Smart-unwrap every mesh in an FBX and export a validated round-trip."""
    fbx_path = os.path.abspath(fbx_path)
    if not os.path.isfile(fbx_path):
        raise FileNotFoundError(f"FBX file not found: {fbx_path}")
    if os.path.splitext(fbx_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx file: {fbx_path}")
    if timeout < 1:
        raise ValueError("Blender timeout must be positive.")
    if overwrite_source and output_path is not None:
        raise ValueError("--overwrite-source cannot be combined with --output.")

    if overwrite_source:
        final_path = fbx_path
    elif output_path is None:
        final_path = _smart_uv_output_path(fbx_path)
    else:
        final_path = os.path.abspath(output_path)

    if os.path.splitext(final_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx output path: {final_path}")
    if _same_path(final_path, fbx_path) and not overwrite_source:
        raise ValueError("Refusing to overwrite the source FBX. Use overwrite_source=True.")
    if os.path.exists(final_path) and not (overwrite or overwrite_source):
        raise FileExistsError(f"Output file exists: {final_path}. Use overwrite=True.")

    values = _validate_smart_uv_options(uv_options)
    output_dir = os.path.dirname(final_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    temp_handle, staging_path = tempfile.mkstemp(
        prefix=f".{os.path.splitext(os.path.basename(final_path))[0]}_",
        suffix=".fbx",
        dir=output_dir,
    )
    os.close(temp_handle)
    os.unlink(staging_path)

    try:
        export_script = generate_fbx_smart_uv_script(
            fbx_path, staging_path, smart_uv_options=values,
        )
        export_run = _run_blender_script(export_script, timeout, "FBX Smart UV export")
        if not os.path.isfile(staging_path):
            raise RuntimeError(f"Blender produced no FBX output: {staging_path}")
        export_summary = _parse_script_marker(export_run["stdout"], "FBX_SMART_UV_RESULT")

        validation_script = generate_fbx_smart_uv_validation_script(fbx_path, staging_path)
        validation_run = _run_blender_script(validation_script, timeout, "FBX round-trip validation")
        validation = _parse_script_marker(
            validation_run["stdout"], "FBX_SMART_UV_VALIDATION",
        )
        if not validation.get("ok"):
            raise RuntimeError(f"FBX round-trip validation failed: {validation}")

        os.replace(staging_path, final_path)
        result = {
            "source_fbx": fbx_path,
            "output_fbx": os.path.abspath(final_path),
            "file_size": os.path.getsize(final_path),
            "blender_version": export_summary.get("blender_version"),
            "mesh_objects": export_summary["mesh_objects"],
            "unique_mesh_datablocks": export_summary["unique_mesh_datablocks"],
            "uv_loop_count": export_summary["uv_loop_count"],
            "source_profile": export_summary["source_profile"],
            "validation": {
                key: value for key, value in validation.items() if key != "ok"
            },
            "smart_uv_options": values,
        }
        return result
    finally:
        if os.path.exists(staging_path):
            os.unlink(staging_path)


def export_fbx_auto_uniform_uv(
    fbx_path: str,
    output_path: Optional[str] = None,
    *,
    overwrite: bool = False,
    overwrite_source: bool = False,
    suffix: Optional[str] = None,
    timeout: int = 300,
    angle_candidates: Optional[Sequence[float]] = None,
    rotate_method: Optional[str] = None,
    cancellation_context=None,
) -> Dict[str, object]:
    """Auto-select a Smart UV angle for uniform checkerboard distortion."""
    fbx_path = os.path.abspath(fbx_path)
    if not os.path.isfile(fbx_path):
        raise FileNotFoundError(f"FBX file not found: {fbx_path}")
    if os.path.splitext(fbx_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx file: {fbx_path}")
    if timeout < 1:
        raise ValueError("Blender timeout must be positive.")
    if overwrite_source and (output_path is not None or suffix is not None):
        raise ValueError("--overwrite-source cannot be combined with --output or --suffix.")
    if output_path is not None and suffix is not None:
        raise ValueError("--output and --suffix cannot be combined.")

    if suffix is not None:
        final_path = _suffix_output_path(fbx_path, suffix)
        replacing_source = False
    elif output_path is not None:
        final_path = os.path.abspath(output_path)
        replacing_source = False
    else:
        # Uniform UV is intentionally an in-place operation by default.
        final_path = fbx_path
        replacing_source = True

    if os.path.splitext(final_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx output path: {final_path}")
    if _same_path(final_path, fbx_path) and not replacing_source:
        raise ValueError("Refusing to overwrite the source FBX. Omit --output or use --suffix.")
    if os.path.exists(final_path) and not (overwrite or replacing_source):
        raise FileExistsError(f"Output file exists: {final_path}. Use overwrite=True.")

    angles = _validate_uniform_uv_angles(angle_candidates)
    smart_uv_options = _validate_smart_uv_options(
        {"rotate_method": rotate_method} if rotate_method is not None else {}
    )
    output_dir = os.path.dirname(final_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    temp_handle, staging_path = tempfile.mkstemp(
        prefix=f".{os.path.splitext(os.path.basename(final_path))[0]}_",
        suffix=".fbx",
        dir=output_dir,
    )
    os.close(temp_handle)
    os.unlink(staging_path)
    if cancellation_context is not None:
        cancellation_context.register_temp_path(staging_path)

    file_started_at = time.monotonic()

    def remaining_file_timeout() -> float:
        return AUTO_UV_FILE_TIMEOUT - (time.monotonic() - file_started_at)

    def timeout_result(stage: str) -> Dict[str, object]:
        return {
            "source_fbx": fbx_path,
            "skipped": True,
            "skip_reason": "processing_timeout",
            "timeout_seconds": AUTO_UV_FILE_TIMEOUT,
            "process_cleanup": None,
            "error": (
                f"Uniform UV processing exceeded {AUTO_UV_FILE_TIMEOUT} seconds "
                f"during {stage}."
            ),
        }

    try:
        remaining = remaining_file_timeout()
        if remaining < 1:
            return timeout_result("startup")
        export_script = generate_fbx_auto_uniform_uv_script(
            fbx_path,
            staging_path,
            angle_candidates=angles,
            smart_uv_options=smart_uv_options,
        )
        try:
            export_run = _run_blender_script(
                export_script,
                min(float(timeout), remaining),
                "FBX auto uniform UV export",
                cancellation_context=cancellation_context,
                input_fbx=fbx_path,
            )
        except subprocess.TimeoutExpired:
            return timeout_result("Blender Uniform UV export")
        if not os.path.isfile(staging_path):
            raise RuntimeError(f"Blender produced no FBX output: {staging_path}")
        export_summary = _parse_script_marker(export_run["stdout"], "FBX_UNIFORM_UV_RESULT")

        remaining = remaining_file_timeout()
        if remaining < 1:
            return timeout_result("FBX export")
        validation_script = generate_fbx_smart_uv_validation_script(fbx_path, staging_path)
        try:
            validation_run = _run_blender_script(
                validation_script,
                min(float(timeout), remaining),
                "FBX round-trip validation",
                cancellation_context=cancellation_context,
                input_fbx=fbx_path,
            )
        except subprocess.TimeoutExpired:
            return timeout_result("FBX round-trip validation")
        validation = _parse_script_marker(
            validation_run["stdout"], "FBX_SMART_UV_VALIDATION",
        )
        if not validation.get("ok"):
            raise RuntimeError(f"FBX round-trip validation failed: {validation}")

        os.replace(staging_path, final_path)
        selected = export_summary["selected_candidate"]
        return {
            "source_fbx": fbx_path,
            "output_fbx": os.path.abspath(final_path),
            "file_size": os.path.getsize(final_path),
            "blender_version": export_summary.get("blender_version"),
            "mesh_objects": export_summary["mesh_objects"],
            "unique_mesh_datablocks": export_summary["unique_mesh_datablocks"],
            "uv_loop_count": export_summary["uv_loop_count"],
            "source_profile": export_summary["source_profile"],
            "objective": "uniform-checker",
            "selected_angle_limit_radians": selected["angle_limit_radians"],
            "selected_angle_limit_degrees": selected["angle_limit_degrees"],
            "selected_metrics": selected["metrics"],
            "candidates": export_summary["candidates"],
            "validation": {
                key: value for key, value in validation.items() if key != "ok"
            },
            "smart_uv_options": export_summary["smart_uv_options"],
        }
    finally:
        if os.path.exists(staging_path):
            os.unlink(staging_path)
        if cancellation_context is not None:
            cancellation_context.unregister_temp_path(staging_path)


def _export_fbx_ministry_auto_uv(
    fbx_path: str,
    output_path: Optional[str] = None,
    *,
    overwrite: bool = False,
    overwrite_source: bool = False,
    suffix: Optional[str] = None,
    timeout: int = 300,
    external_timeout: int = 120,
    executable_path: Optional[str] = None,
    topology_prefilter: Optional[bool] = None,
    topology_prefilter_level: Optional[str] = None,
    cancellation_context=None,
    **auto_uv_options,
) -> Dict[str, object]:
    """Run Ministry of Flat AutoUV for every unique mesh in an FBX."""
    fbx_path = os.path.abspath(fbx_path)
    if not os.path.isfile(fbx_path):
        raise FileNotFoundError(f"FBX file not found: {fbx_path}")
    if os.path.splitext(fbx_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx file: {fbx_path}")
    if timeout < 1 or external_timeout < 1:
        raise ValueError("Timeout values must be positive.")
    if overwrite_source and (output_path is not None or suffix is not None):
        raise ValueError("--overwrite-source cannot be combined with --output or --suffix.")
    if output_path is not None and suffix is not None:
        raise ValueError("--output and --suffix cannot be combined.")

    if suffix is not None:
        final_path = _suffix_output_path(fbx_path, suffix)
        replacing_source = False
    elif output_path is not None:
        final_path = os.path.abspath(output_path)
        replacing_source = False
    else:
        final_path = fbx_path
        replacing_source = True

    if os.path.splitext(final_path)[1].lower() != ".fbx":
        raise ValueError(f"Expected an .fbx output path: {final_path}")
    if _same_path(final_path, fbx_path) and not replacing_source:
        raise ValueError("Refusing to overwrite the source FBX. Omit --output or use --suffix.")
    if os.path.exists(final_path) and not (overwrite or replacing_source):
        raise FileExistsError(f"Output file exists: {final_path}. Use overwrite=True.")

    options = _validate_ministry_of_flat_options(auto_uv_options)
    resolved_prefilter_level = _resolve_topology_prefilter_level(
        topology_prefilter_level,
        topology_prefilter,
    )
    executable = resolve_ministry_of_flat_executable(executable_path)
    output_dir = os.path.dirname(final_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    temp_handle, staging_path = tempfile.mkstemp(
        prefix=f".{os.path.splitext(os.path.basename(final_path))[0]}_",
        suffix=".fbx",
        dir=output_dir,
    )
    os.close(temp_handle)
    os.unlink(staging_path)
    if cancellation_context is not None:
        cancellation_context.register_temp_path(staging_path)
    temporary_root = None
    if cancellation_context is not None:
        temporary_root = cancellation_context.create_temp_dir("blender-task-")

    file_started_at = time.monotonic()

    def remaining_file_timeout() -> float:
        return AUTO_UV_FILE_TIMEOUT - (time.monotonic() - file_started_at)

    def timeout_result(stage: str) -> Dict[str, object]:
        return {
            "source_fbx": fbx_path,
            "skipped": True,
            "skip_reason": "processing_timeout",
            "timeout_seconds": AUTO_UV_FILE_TIMEOUT,
            "process_cleanup": None,
            "error": (
                f"AutoUV processing exceeded {AUTO_UV_FILE_TIMEOUT} seconds "
                f"during {stage}."
            ),
        }

    try:
        remaining = remaining_file_timeout()
        if remaining < 1:
            return timeout_result("startup")
        export_script = generate_fbx_auto_uv_script(
            fbx_path,
            staging_path,
            executable_path=executable,
            external_timeout=external_timeout,
            file_timeout=timeout,
            auto_uv_options=options,
            topology_prefilter_level=resolved_prefilter_level,
            temporary_root=temporary_root,
        )
        try:
            export_run = _run_blender_script(
                export_script,
                min(float(timeout), remaining),
                "FBX AutoUV export",
                cancellation_context=cancellation_context,
                input_fbx=fbx_path,
            )
        except subprocess.TimeoutExpired:
            return timeout_result("Blender AutoUV export")
        if not os.path.isfile(staging_path):
            skip_info = None
            for marker in ("FBX_AUTO_UV_SKIP", "FBX_AUTO_UV_PREFLIGHT"):
                try:
                    candidate = _parse_script_marker(export_run["stdout"], marker)
                except RuntimeError:
                    continue
                if isinstance(candidate, dict) and candidate.get("skipped"):
                    skip_info = candidate
                    break
            if isinstance(skip_info, dict):
                return {
                    "source_fbx": fbx_path,
                    "skipped": True,
                    "skip_reason": skip_info.get("skip_reason", "topology_risk"),
                    "error": skip_info.get("error"),
                    "timeout_seconds": skip_info.get("timeout_seconds"),
                    "process_cleanup": skip_info.get("process_cleanup"),
                    "preflight": skip_info.get("preflight", skip_info),
                }
            raise RuntimeError(f"Blender produced no FBX output: {staging_path}")
        export_summary = _parse_script_marker(export_run["stdout"], "FBX_AUTO_UV_RESULT")

        remaining = remaining_file_timeout()
        if remaining < 1:
            return timeout_result("FBX export")
        validation_script = generate_fbx_smart_uv_validation_script(fbx_path, staging_path)
        try:
            validation_run = _run_blender_script(
                validation_script,
                min(float(timeout), remaining),
                "FBX AutoUV round-trip validation",
                cancellation_context=cancellation_context,
                input_fbx=fbx_path,
            )
        except subprocess.TimeoutExpired:
            return timeout_result("FBX round-trip validation")
        validation = _parse_script_marker(
            validation_run["stdout"], "FBX_SMART_UV_VALIDATION",
        )
        if not validation.get("ok"):
            raise RuntimeError(f"FBX round-trip validation failed: {validation}")

        os.replace(staging_path, final_path)
        return {
            "source_fbx": fbx_path,
            "output_fbx": os.path.abspath(final_path),
            "file_size": os.path.getsize(final_path),
            "blender_version": export_summary.get("blender_version"),
            "mesh_objects": export_summary["mesh_objects"],
            "unique_mesh_datablocks": export_summary["unique_mesh_datablocks"],
            "processed_mesh_objects": export_summary["processed_mesh_objects"],
            "normalized_meshes": export_summary.get("normalized_meshes", 0),
            "external_call_count": export_summary.get("external_call_count", 0),
            "merge_meshes_requested": export_summary.get("merge_meshes_requested"),
            "merge_meshes_applied": export_summary.get("merge_meshes_applied"),
            "merge_mesh_count": export_summary.get("merge_mesh_count", 0),
            "normalize_uv_requested": export_summary.get("normalize_uv_requested"),
            "normalize_uv_applied": export_summary.get("normalize_uv_applied"),
            "normalization_skipped_reason": export_summary.get("normalization_skipped_reason"),
            "normalization_margin": export_summary.get("normalization_margin"),
            "uv_loop_count": export_summary["uv_loop_count"],
            "active_uv_maps": export_summary["active_uv_maps"],
            "external_executable": executable,
            "external_warnings": export_summary.get("external_warnings", []),
            "preflight": export_summary.get("preflight", {}),
            "auto_uv_options": options,
            "validation": {
                key: value for key, value in validation.items() if key != "ok"
            },
        }
    finally:
        if os.path.exists(staging_path):
            os.unlink(staging_path)
        if cancellation_context is not None and temporary_root:
            shutil.rmtree(temporary_root, ignore_errors=True)
            cancellation_context.unregister_temp_path(temporary_root)
        if cancellation_context is not None:
            cancellation_context.unregister_temp_path(staging_path)


def _validate_auto_uv_algorithm(algorithm: str) -> str:
    value = str(algorithm or "").strip().lower()
    if value not in AUTO_UV_ALGORITHMS:
        choices = ", ".join(AUTO_UV_ALGORITHMS)
        raise ValueError(f"Unknown AutoUV algorithm {algorithm!r}; choose one of: {choices}")
    return value


def export_fbx_auto_uv(
    fbx_path: str,
    output_path: Optional[str] = None,
    *,
    algorithm: str = "autouv",
    overwrite: bool = False,
    overwrite_source: bool = False,
    suffix: Optional[str] = None,
    timeout: int = 300,
    external_timeout: int = 120,
    executable_path: Optional[str] = None,
    topology_prefilter: Optional[bool] = None,
    topology_prefilter_level: Optional[str] = None,
    cancellation_context=None,
    angle_candidates: Optional[Sequence[float]] = None,
    rotate_method: Optional[str] = None,
    **auto_uv_options,
) -> Dict[str, object]:
    """Run one selected AutoUV algorithm and export a validated FBX."""
    selected_algorithm = _validate_auto_uv_algorithm(algorithm)
    if selected_algorithm == "uniform":
        unsupported = {
            key: value for key, value in auto_uv_options.items() if value is not None
        }
        if executable_path is not None or external_timeout not in (10, 120) or unsupported:
            raise ValueError(
                "Ministry of Flat options cannot be used with the uniform algorithm."
            )
        result = export_fbx_auto_uniform_uv(
            fbx_path,
            output_path,
            overwrite=overwrite,
            overwrite_source=overwrite_source,
            suffix=suffix,
            timeout=timeout,
            angle_candidates=angle_candidates,
            rotate_method=rotate_method,
            cancellation_context=cancellation_context,
        )
        result["algorithm"] = selected_algorithm
        return result

    if angle_candidates is not None or rotate_method is not None:
        raise ValueError("Uniform UV options cannot be used with the autouv algorithm.")
    result = _export_fbx_ministry_auto_uv(
        fbx_path,
        output_path,
        overwrite=overwrite,
        overwrite_source=overwrite_source,
        suffix=suffix,
        timeout=timeout,
        external_timeout=external_timeout,
        executable_path=executable_path,
        topology_prefilter=topology_prefilter,
        topology_prefilter_level=topology_prefilter_level,
        cancellation_context=cancellation_context,
        **auto_uv_options,
    )
    result["algorithm"] = selected_algorithm
    return result


def _normalize_batch_inputs(fbx_paths: Sequence[str]) -> list[str]:
    if not fbx_paths:
        raise ValueError("At least one FBX input is required.")
    normalized = []
    seen = set()
    for value in fbx_paths:
        path = os.path.abspath(os.path.expanduser(os.fspath(value)))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"FBX file not found: {path}")
        if os.path.splitext(path)[1].lower() != ".fbx":
            raise ValueError(f"Expected an .fbx file: {path}")
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    return normalized


def _batch_output_path(source_path: str, output_dir: str, suffix: Optional[str]) -> str:
    directory = os.path.abspath(os.path.expanduser(output_dir))
    stem = os.path.splitext(os.path.basename(source_path))[0]
    suffix_value = suffix or ""
    return os.path.join(directory, f"{stem}{suffix_value}.fbx")


def _validate_batch_output_paths(
    inputs: Sequence[str],
    *,
    output_dir: Optional[str],
    output_path: Optional[str],
    suffix: Optional[str],
) -> tuple[Optional[str], ...]:
    """Resolve batch destinations and reject collisions before work starts."""
    if output_path is not None:
        planned = (os.path.abspath(output_path),)
    elif output_dir is not None:
        planned = tuple(
            _batch_output_path(source_path, output_dir, suffix)
            for source_path in inputs
        )
    elif suffix is not None:
        planned = tuple(_suffix_output_path(source_path, suffix) for source_path in inputs)
    else:
        planned = tuple(None for _ in inputs)

    concrete = [os.path.normcase(path) for path in planned if path is not None]
    if len(concrete) != len(set(concrete)):
        raise ValueError(
            "Batch inputs resolve to duplicate output paths; use unique filenames, "
            "a different output directory, or a different suffix."
        )
    return planned


def export_fbx_auto_uv_batch(
    fbx_paths: Sequence[str],
    *,
    algorithm: str = "autouv",
    output_dir: Optional[str] = None,
    output_path: Optional[str] = None,
    overwrite: bool = False,
    overwrite_source: bool = False,
    suffix: Optional[str] = None,
    timeout: int = 300,
    external_timeout: int = 120,
    executable_path: Optional[str] = None,
    topology_prefilter: Optional[bool] = None,
    topology_prefilter_level: Optional[str] = None,
    angle_candidates: Optional[Sequence[float]] = None,
    rotate_method: Optional[str] = None,
    progress_callback: Optional[Callable[[Dict[str, object]], None]] = None,
    jobs: int = 2,
    cancellation_context=None,
    **auto_uv_options,
) -> Dict[str, object]:
    """Process one or more FBX files and return an aggregate result."""
    from cli_anything.blender.utils.blender_backend import CancellationRequested

    selected_algorithm = _validate_auto_uv_algorithm(algorithm)
    inputs = _normalize_batch_inputs(fbx_paths)
    try:
        requested_jobs = int(jobs)
    except (TypeError, ValueError) as error:
        raise ValueError("jobs must be a positive integer.") from error
    if requested_jobs < 1:
        raise ValueError("jobs must be a positive integer.")
    effective_jobs = min(requested_jobs, len(inputs))
    resolved_prefilter_level = (
        _resolve_topology_prefilter_level(topology_prefilter_level, topology_prefilter)
        if selected_algorithm == "autouv"
        else "off"
    )
    if output_path is not None and len(inputs) != 1:
        raise ValueError("--output can only be used with a single FBX input; use --output-dir for batches.")
    if output_path is not None and (output_dir is not None or suffix is not None):
        raise ValueError("--output cannot be combined with --output-dir or --suffix.")
    if overwrite_source and (output_path is not None or output_dir is not None or suffix is not None):
        raise ValueError("--overwrite-source cannot be combined with batch output options.")
    planned_outputs = _validate_batch_output_paths(
        inputs,
        output_dir=output_dir,
        output_path=output_path,
        suffix=suffix,
    )

    def notify(event: str, **payload: object) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback({
                "event": event,
                "algorithm": selected_algorithm,
                **payload,
            })
        except Exception:
            # Progress reporting must never change the processing result.
            return

    cancellation_notified = False

    def cancellation_requested() -> bool:
        nonlocal cancellation_notified
        if cancellation_context is None or not cancellation_context.is_cancelled():
            return False
        if not cancellation_notified:
            notify("batch_cancel_requested", total=total)
            cancellation_notified = True
        cancellation_context.terminate_all()
        return True

    total = len(inputs)
    notify(
        "batch_started",
        total=total,
        jobs=requested_jobs,
        effective_jobs=effective_jobs,
        topology_prefilter_level=resolved_prefilter_level,
    )
    results: list[Optional[Dict[str, object]]] = [None] * total

    def process_one(index: int, source_path: str, per_file_output: Optional[str]) -> Dict[str, object]:
        started_at = time.monotonic()
        try:
            if cancellation_requested():
                raise CancellationRequested("batch cancellation requested before file start")
            result = export_fbx_auto_uv(
                source_path,
                per_file_output,
                algorithm=selected_algorithm,
                overwrite=overwrite,
                overwrite_source=overwrite_source,
                suffix=None if per_file_output is not None else suffix,
                timeout=timeout,
                external_timeout=external_timeout,
                executable_path=executable_path,
                topology_prefilter=None,
                topology_prefilter_level=resolved_prefilter_level,
                cancellation_context=cancellation_context,
                angle_candidates=angle_candidates,
                rotate_method=rotate_method,
                **auto_uv_options,
            )
            duration_seconds = round(time.monotonic() - started_at, 3)
            if result.get("skipped"):
                item = {
                    "input_fbx": source_path,
                    "ok": False,
                    "skipped": True,
                    "skip_reason": result.get("skip_reason"),
                    "error": result.get("error"),
                    "timeout_seconds": result.get("timeout_seconds"),
                    "process_cleanup": result.get("process_cleanup"),
                    "preflight": result.get("preflight", {}),
                    "duration_seconds": duration_seconds,
                    "result": result,
                }
            else:
                item = {
                    "input_fbx": source_path,
                    "output_fbx": result.get("output_fbx"),
                    "ok": True,
                    "preflight": result.get("preflight", {}),
                    "warnings": result.get("external_warnings", []),
                    "duration_seconds": duration_seconds,
                    "result": result,
                }
            return {
                "index": index,
                "input_fbx": source_path,
                "item": item,
                "event": {
                    "output_fbx": result.get("output_fbx"),
                    "ok": not result.get("skipped"),
                    "skipped": bool(result.get("skipped")),
                    "skip_reason": result.get("skip_reason"),
                    "error": result.get("error"),
                    "timeout_seconds": result.get("timeout_seconds"),
                    "process_cleanup": result.get("process_cleanup"),
                    "risk_score": result.get("preflight", {}).get("risk_score"),
                    "preflight": result.get("preflight"),
                    "warnings": result.get("external_warnings", []),
                    "duration_seconds": duration_seconds,
                },
            }
        except CancellationRequested as error:
            duration_seconds = round(time.monotonic() - started_at, 3)
            return {
                "index": index,
                "input_fbx": source_path,
                "item": {
                    "input_fbx": source_path,
                    "ok": False,
                    "cancelled": True,
                    "skip_reason": "cancelled",
                    "error": str(error),
                    "process_cleanup": list(cancellation_context.cleanup_reports)
                    if cancellation_context else [],
                    "duration_seconds": duration_seconds,
                },
                "event": {
                    "ok": False,
                    "cancelled": True,
                    "skipped": False,
                    "skip_reason": "cancelled",
                    "error": str(error),
                    "process_cleanup": list(cancellation_context.cleanup_reports)
                    if cancellation_context else [],
                    "duration_seconds": duration_seconds,
                },
            }
        except Exception as error:
            duration_seconds = round(time.monotonic() - started_at, 3)
            if cancellation_requested():
                return {
                    "index": index,
                    "input_fbx": source_path,
                    "item": {
                        "input_fbx": source_path,
                        "ok": False,
                        "cancelled": True,
                        "skip_reason": "cancelled",
                        "error": str(error),
                        "process_cleanup": list(cancellation_context.cleanup_reports)
                        if cancellation_context else [],
                        "duration_seconds": duration_seconds,
                    },
                    "event": {
                        "ok": False,
                        "cancelled": True,
                        "skipped": False,
                        "skip_reason": "cancelled",
                        "error": str(error),
                        "duration_seconds": duration_seconds,
                    },
                }
            return {
                "index": index,
                "input_fbx": source_path,
                "item": {
                    "input_fbx": source_path,
                    "ok": False,
                    "error": str(error),
                    "duration_seconds": duration_seconds,
                },
                "event": {
                    "ok": False,
                    "error": str(error),
                    "duration_seconds": duration_seconds,
                },
            }

    def submit_next(executor, active, next_index: int) -> int:
        if next_index > total:
            return next_index
        source_path = inputs[next_index - 1]
        per_file_output = planned_outputs[next_index - 1]
        notify(
            "file_started",
            index=next_index,
            total=total,
            input_fbx=source_path,
            active_jobs=len(active) + 1,
        )
        future = executor.submit(process_one, next_index, source_path, per_file_output)
        active[future] = next_index
        return next_index + 1

    completed_count = 0
    active = {}
    next_index = 1
    with ThreadPoolExecutor(max_workers=effective_jobs) as executor:
        while next_index <= total and len(active) < effective_jobs and not cancellation_requested():
            next_index = submit_next(executor, active, next_index)
        while active:
            if cancellation_requested():
                # Active workers observe the same event in their managed Popen loop.
                cancellation_context.terminate_all()
            done, _ = wait(tuple(active), timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                active.pop(future)
                processed = future.result()
                completed_count += 1
                index = int(processed["index"])
                results[index - 1] = processed["item"]
                notify(
                    "file_finished",
                    index=index,
                    total=total,
                    completed_count=completed_count,
                    input_fbx=processed["input_fbx"],
                    active_jobs=len(active),
                    **processed["event"],
                )
                if next_index <= total and not cancellation_requested():
                    next_index = submit_next(executor, active, next_index)

        while next_index <= total:
            source_path = inputs[next_index - 1]
            item = {
                "input_fbx": source_path,
                "ok": False,
                "cancelled": True,
                "skip_reason": "cancelled",
                "error": "Batch cancelled before this file started.",
                "process_cleanup": list(cancellation_context.cleanup_reports)
                if cancellation_context else [],
            }
            results[next_index - 1] = item
            completed_count += 1
            notify(
                "file_finished",
                index=next_index,
                total=total,
                completed_count=completed_count,
                input_fbx=source_path,
                active_jobs=0,
                ok=False,
                cancelled=True,
                skipped=False,
                skip_reason="cancelled",
                error=item["error"],
                process_cleanup=list(cancellation_context.cleanup_reports)
                if cancellation_context else [],
            )
            next_index += 1

    final_results = [item for item in results if item is not None]
    success_count = sum(1 for item in final_results if item["ok"])
    skipped_count = sum(1 for item in final_results if item.get("skipped"))
    cancelled_count = sum(1 for item in final_results if item.get("cancelled"))
    failure_count = len(results) - success_count - skipped_count - cancelled_count
    cancelled = bool(cancelled_count or cancellation_requested())
    notify(
        "batch_finished",
        total=total,
        jobs=requested_jobs,
        effective_jobs=effective_jobs,
        success_count=success_count,
        failure_count=failure_count,
        skipped_count=skipped_count,
        cancelled=cancelled,
        cancelled_count=cancelled_count,
        process_cleanup=list(cancellation_context.cleanup_reports)
        if cancellation_context else [],
    )
    return {
        "algorithm": selected_algorithm,
        "total": total,
        "jobs": requested_jobs,
        "effective_jobs": effective_jobs,
        "success_count": success_count,
        "failure_count": failure_count,
        "skipped_count": skipped_count,
        "cancelled": cancelled,
        "cancelled_count": cancelled_count,
        "process_cleanup": list(cancellation_context.cleanup_reports)
        if cancellation_context else [],
        "results": final_results,
    }


def generate_fbx_auto_uv_script(
    fbx_path: str,
    output_path: str,
    *,
    executable_path: str,
    external_timeout: int = 120,
    file_timeout: int = AUTO_UV_FILE_TIMEOUT,
    auto_uv_options: Optional[Dict[str, object]] = None,
    topology_prefilter: Optional[bool] = None,
    topology_prefilter_level: Optional[str] = None,
    temporary_root: Optional[str] = None,
) -> str:
    """Generate the standalone Blender script used by :func:`export_fbx_auto_uv`."""
    options = _validate_ministry_of_flat_options(auto_uv_options or {})
    resolved_prefilter_level = _resolve_topology_prefilter_level(
        topology_prefilter_level,
        topology_prefilter,
    )
    config = repr({
        "fbx_path": os.path.abspath(fbx_path),
        "output_path": os.path.abspath(output_path),
        "executable_path": os.path.abspath(executable_path),
        "external_timeout": int(external_timeout),
        "auto_uv_options": options,
        "single_tile_margin": AUTO_UV_SINGLE_TILE_MARGIN,
        "topology_prefilter": resolved_prefilter_level != "off",
        "topology_prefilter_level": resolved_prefilter_level,
        "topology_risk_version": TOPOLOGY_RISK_VERSION,
        "topology_risk_threshold": TOPOLOGY_RISK_THRESHOLD,
        "file_timeout": int(file_timeout),
        "temporary_root": os.path.abspath(temporary_root) if temporary_root else None,
    })
    script = r'''
import bpy
import bmesh
import json
import inspect
import os
import re
import shutil
import subprocess
import tempfile
import time
from io_scene_fbx import import_fbx, parse_fbx
from io_scene_fbx.fbx_utils import RIGHT_HAND_AXES
from mathutils import Vector

CONFIG = __CONFIG__
FILE_STARTED_AT = time.monotonic()
LAST_PROCESS_CLEANUP = None

class FileProcessingTimeout(RuntimeError):
    pass

def remaining_file_time():
    return CONFIG['file_timeout'] - (time.monotonic() - FILE_STARTED_AT)

def ensure_file_time(stage):
    remaining = remaining_file_time()
    if remaining <= 0:
        raise FileProcessingTimeout(
            'AutoUV processing exceeded ' + str(CONFIG['file_timeout']) +
            ' seconds during ' + stage + '.'
        )
    return remaining

def topology_risk_level(score):
    if score >= CONFIG['topology_risk_threshold']:
        return 'high'
    if score >= 4:
        return 'medium'
    return 'low'

def score_topology_metrics(metrics):
    vertices = int(metrics.get('vertices', 0) or 0)
    polygons = int(metrics.get('polygons', 0) or 0)
    loops = int(metrics.get('loops', 0) or 0)
    ngons = int(metrics.get('ngons', 0) or 0)
    max_polygon_vertices = int(metrics.get('max_polygon_vertices', 0) or 0)
    boundary_edges = int(metrics.get('boundary_edges', 0) or 0)
    edge_count = max(int(metrics.get('edges', 0) or 0), 1)
    duplicate_groups = int(metrics.get('duplicate_position_groups', 0) or 0)
    zero_area_faces = int(metrics.get('zero_area_faces', 0) or 0)
    interior_non_manifold_edges = int(metrics.get('interior_non_manifold_edges', 0) or 0)
    boundary_edge_ratio = float(metrics.get('boundary_edge_ratio', boundary_edges / edge_count) or 0.0)
    ngon_ratio = float(metrics.get('ngon_ratio', ngons / max(polygons, 1)) or 0.0)
    triggered = []

    def add_rule(code, reason, value, threshold, points):
        triggered.append({
            'code': code,
            'reason': reason,
            'value': value,
            'threshold': threshold,
            'points': points,
        })

    if vertices >= 3500:
        add_rule('vertices_high', 'vertices >= 3500', vertices, 3500, 3)
    if polygons >= 3000:
        add_rule('polygons_high', 'polygons >= 3000', polygons, 3000, 2)
    if loops >= 12000:
        add_rule('loops_high', 'loops >= 12000', loops, 12000, 2)
    if boundary_edge_ratio >= 0.22:
        add_rule('boundary_ratio_very_high', 'boundary_edge_ratio >= 0.22', boundary_edge_ratio, 0.22, 3)
    elif boundary_edge_ratio >= 0.18:
        add_rule('boundary_ratio_high', 'boundary_edge_ratio >= 0.18', boundary_edge_ratio, 0.18, 2)
    if boundary_edges >= 200:
        add_rule('boundary_edges_high', 'boundary_edges >= 200', boundary_edges, 200, 1)
    if ngon_ratio >= 0.20:
        add_rule('ngon_ratio_very_high', 'ngon_ratio >= 0.20', ngon_ratio, 0.20, 3)
    elif ngon_ratio >= 0.10:
        add_rule('ngon_ratio_high', 'ngon_ratio >= 0.10', ngon_ratio, 0.10, 2)
    if max_polygon_vertices >= 13:
        add_rule('max_polygon_vertices_very_high', 'max_polygon_vertices >= 13', max_polygon_vertices, 13, 3)
    elif max_polygon_vertices >= 7:
        add_rule('max_polygon_vertices_high', 'max_polygon_vertices >= 7', max_polygon_vertices, 7, 2)
    elif max_polygon_vertices >= 5:
        add_rule('max_polygon_vertices_elevated', 'max_polygon_vertices >= 5', max_polygon_vertices, 5, 1)
    if duplicate_groups >= 3:
        add_rule('duplicate_positions', 'duplicate_position_groups >= 3', duplicate_groups, 3, 1)
    if zero_area_faces > 0:
        add_rule('zero_area_faces', 'zero_area_faces > 0', zero_area_faces, 0, 4)
    if interior_non_manifold_edges >= 50:
        add_rule('interior_non_manifold_edges', 'interior_non_manifold_edges >= 50', interior_non_manifold_edges, 50, 2)
    if boundary_edge_ratio >= 0.18 and ngons >= 20:
        add_rule(
            'open_boundary_ngon_combo',
            'boundary_edge_ratio >= 0.18 and ngons >= 20',
            {'boundary_edge_ratio': boundary_edge_ratio, 'ngons': ngons},
            {'boundary_edge_ratio': 0.18, 'ngons': 20},
            2,
        )
    if polygons < 1000 and boundary_edge_ratio >= 0.18 and ngons >= 20:
        add_rule(
            'small_open_complex_mesh',
            'polygons < 1000 and boundary_edge_ratio >= 0.18 and ngons >= 20',
            {'polygons': polygons, 'boundary_edge_ratio': boundary_edge_ratio, 'ngons': ngons},
            {'polygons': 1000, 'boundary_edge_ratio': 0.18, 'ngons': 20},
            2,
        )
    score = sum(int(rule['points']) for rule in triggered)
    return {
        'risk_version': CONFIG['topology_risk_version'],
        'risk_score': score,
        'risk_level': topology_risk_level(score),
        'ngon_ratio': ngon_ratio,
        'interior_non_manifold_edges': interior_non_manifold_edges,
        'reasons': [rule['reason'] for rule in triggered],
        'triggered_rules': triggered,
    }

def analyze_topology(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        duplicate_positions = {}
        for vertex in mesh.vertices:
            key = tuple(round(float(component), 7) for component in vertex.co)
            duplicate_positions[key] = duplicate_positions.get(key, 0) + 1
        duplicate_groups = sum(1 for count in duplicate_positions.values() if count > 1)
        edges = max(len(mesh.edges), 1)
        boundary_edges = sum(1 for edge in bm.edges if edge.is_boundary)
        non_manifold_edges = sum(1 for edge in bm.edges if not edge.is_manifold)
        interior_non_manifold_edges = sum(
            1 for edge in bm.edges if not edge.is_boundary and not edge.is_manifold
        )
        zero_area_faces = sum(1 for polygon in mesh.polygons if polygon.area <= 1.0e-12)
        ngon_count = sum(1 for polygon in mesh.polygons if len(polygon.vertices) > 4)
        max_polygon_vertices = max((len(polygon.vertices) for polygon in mesh.polygons), default=0)
        metrics = {
            'object': obj.name,
            'vertices': len(mesh.vertices),
            'polygons': len(mesh.polygons),
            'loops': len(mesh.loops),
            'edges': len(mesh.edges),
            'ngons': ngon_count,
            'max_polygon_vertices': max_polygon_vertices,
            'boundary_edges': boundary_edges,
            'non_manifold_edges': non_manifold_edges,
            'boundary_edge_ratio': boundary_edges / edges,
            'duplicate_position_groups': duplicate_groups,
            'zero_area_faces': zero_area_faces,
            'interior_non_manifold_edges': interior_non_manifold_edges,
        }
        metrics['ngon_ratio'] = ngon_count / max(len(mesh.polygons), 1)
        metrics.update(score_topology_metrics(metrics))
        return metrics
    finally:
        bm.free()

def preflight_topology(unique_meshes):
    reports = [analyze_topology(obj) for obj in unique_meshes]
    highest = max(reports, key=lambda item: item['risk_score']) if reports else None
    level = CONFIG['topology_prefilter_level']
    skipped = bool(
        level == 'high' and highest and highest['risk_level'] == 'high'
        or level == 'medium' and highest and highest['risk_level'] in {'medium', 'high'}
    )
    return {
        'enabled': bool(CONFIG['topology_prefilter']),
        'prefilter_level': level,
        'skipped': skipped,
        'risk_version': CONFIG['topology_risk_version'],
        'risk_threshold': CONFIG['topology_risk_threshold'],
        'risk_score': highest['risk_score'] if highest else 0,
        'risk_level': highest['risk_level'] if highest else 'low',
        'mesh_objects': len(unique_meshes),
        'highest_risk_mesh': highest['object'] if highest else None,
        'reasons': highest['reasons'] if highest else [],
        'meshes': reports,
    }

_light_source = inspect.getsource(import_fbx.blen_read_light)
if 'lamp.cycles.cast_shadow = lamp.use_shadow' in _light_source:
    _light_source = _light_source.replace(
        '        lamp.cycles.cast_shadow = lamp.use_shadow',
        '        try:\n            lamp.cycles.cast_shadow = lamp.use_shadow\n        except AttributeError:\n            pass',
    )
    _light_namespace = dict(import_fbx.__dict__)
    exec(compile(_light_source, '<fbx_light_compat>', 'exec'), _light_namespace)
    import_fbx.blen_read_light = _light_namespace['blen_read_light']

def source_profile(path):
    root, version = parse_fbx.parse(path)
    settings = import_fbx.elem_find_first(root, b'GlobalSettings')
    props = import_fbx.elem_find_first(settings, b'Properties70') if settings else None
    if props is None:
        raise RuntimeError('FBX has no GlobalSettings/Properties70 block.')
    unit = float(import_fbx.elem_props_get_number(props, b'UnitScaleFactor', 1.0))
    up = (int(import_fbx.elem_props_get_integer(props, b'UpAxis', 2)), int(import_fbx.elem_props_get_integer(props, b'UpAxisSign', 1)))
    forward = (int(import_fbx.elem_props_get_integer(props, b'FrontAxis', 1)), int(import_fbx.elem_props_get_integer(props, b'FrontAxisSign', 1)))
    coord = (int(import_fbx.elem_props_get_integer(props, b'CoordAxis', 0)), int(import_fbx.elem_props_get_integer(props, b'CoordAxisSign', 1)))
    axis_key = (up, forward, coord)
    axis_map = {value: key for key, value in RIGHT_HAND_AXES.items()}
    if axis_key not in axis_map:
        raise RuntimeError('Source FBX axis system is not representable by Blender FBX exporter: ' + repr(axis_key))
    axis_up, axis_forward = axis_map[axis_key]
    if unit <= 0:
        raise RuntimeError('Source FBX UnitScaleFactor must be positive.')
    return {'axis_up': axis_up, 'axis_forward': axis_forward, 'unit_scale_factor': unit, 'version': version}

def boolean_text(value):
    return 'TRUE' if value else 'FALSE'

def safe_name(name):
    value = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._')
    return value or 'mesh'

def set_only_selected(obj):
    for candidate in list(bpy.context.view_layer.objects):
        if candidate is not None:
            candidate.select_set(candidate == obj)
    bpy.context.view_layer.objects.active = obj

def remove_imported_objects(objects):
    for obj in objects:
        mesh = getattr(obj, 'data', None)
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.context.view_layer.update()

def validate_topology(source_mesh, imported_mesh):
    if len(source_mesh.vertices) != len(imported_mesh.vertices):
        raise RuntimeError('Vertex count changed during OBJ round-trip.')
    if len(source_mesh.polygons) != len(imported_mesh.polygons):
        raise RuntimeError('Polygon count changed during OBJ round-trip.')
    if len(source_mesh.loops) != len(imported_mesh.loops):
        raise RuntimeError('Loop count changed during OBJ round-trip.')
    for source_polygon, imported_polygon in zip(source_mesh.polygons, imported_mesh.polygons):
        if source_polygon.loop_total != imported_polygon.loop_total:
            raise RuntimeError('Polygon loop structure changed during OBJ round-trip.')
    for source_loop, imported_loop in zip(source_mesh.loops, imported_mesh.loops):
        if source_loop.vertex_index != imported_loop.vertex_index:
            raise RuntimeError('Vertex ordering changed during OBJ round-trip.')

def normalize_uv_layer_to_single_tile(uv_layer, margin):
    if uv_layer is None or not uv_layer.data:
        return False
    min_x = min(item.uv.x for item in uv_layer.data)
    max_x = max(item.uv.x for item in uv_layer.data)
    min_y = min(item.uv.y for item in uv_layer.data)
    max_y = max(item.uv.y for item in uv_layer.data)
    span_x = max_x - min_x
    span_y = max_y - min_y
    largest_span = max(span_x, span_y)
    if largest_span <= 1.0e-12:
        return False
    usable_size = max(1.0e-6, 1.0 - (2.0 * margin))
    scale = usable_size / largest_span
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    for item in uv_layer.data:
        item.uv.x = (item.uv.x - center_x) * scale + 0.5
        item.uv.y = (item.uv.y - center_y) * scale + 0.5
    return True

def export_selected_obj(obj, path):
    ensure_file_time('temporary OBJ export')
    set_only_selected(obj)
    bpy.ops.wm.obj_export(
        filepath=path,
        export_selected_objects=True,
        export_materials=False,
    )
    if not os.path.isfile(path):
        raise RuntimeError('Blender did not produce the temporary OBJ.')

def terminate_process_tree(process):
    cleanup = {'attempted': True, 'ok': False, 'method': None, 'details': None}
    if os.name == 'nt':
        cleanup['method'] = 'taskkill'
        try:
            result = subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=5,
                check=False,
            )
            cleanup['ok'] = result.returncode == 0
            cleanup['details'] = (result.stderr or result.stdout or '').strip()[-500:]
        except Exception as error:
            cleanup['details'] = str(error)
    else:
        cleanup['method'] = 'process_group'
        try:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
            cleanup['ok'] = True
        except Exception as error:
            cleanup['details'] = str(error)
    try:
        process.kill()
    except Exception:
        pass
    try:
        process.communicate(timeout=2)
    except Exception as error:
        if cleanup['details']:
            cleanup['details'] += '; ' + str(error)
        else:
            cleanup['details'] = str(error)
    return cleanup

def run_unwrap(input_path, output_path):
    global LAST_PROCESS_CLEANUP
    options = CONFIG['auto_uv_options']
    remaining = ensure_file_time('external AutoUV process')
    cleanup_margin = 0.25
    if remaining < cleanup_margin + 0.1:
        raise FileProcessingTimeout(
            'AutoUV processing exceeded ' + str(CONFIG['file_timeout']) +
            ' seconds before external AutoUV process.'
        )
    effective_timeout = min(
        float(CONFIG['external_timeout']),
        remaining - cleanup_margin,
    )
    command = [
        CONFIG['executable_path'], input_path, output_path,
        '-resolution', str(options['resolution']),
        '-separate', boolean_text(options['separate_hard_edges']),
        '-aspect', str(options['aspect']),
        '-normals', boolean_text(options['use_normals']),
        '-udims', str(options['udims']),
        '-overlap', boolean_text(options['overlap_identical']),
        '-mirror', boolean_text(options['overlap_mirrored']),
        '-worldscale', boolean_text(options['world_scale']),
        '-density', str(options['density']),
    ]
    popen_kwargs = {
        'cwd': os.path.dirname(CONFIG['executable_path']),
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
        'text': True,
        'encoding': 'utf-8',
        'errors': 'replace',
    }
    if os.name == 'nt':
        popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    else:
        popen_kwargs['start_new_session'] = True
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=max(0.1, effective_timeout))
        completed_returncode = process.returncode
    except subprocess.TimeoutExpired:
        cleanup = terminate_process_tree(process)
        cleanup['output_removed'] = False
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                cleanup['output_removed'] = True
            except Exception as error:
                cleanup['output_remove_error'] = str(error)
        LAST_PROCESS_CLEANUP = cleanup
        if remaining_file_time() <= 0:
            raise FileProcessingTimeout(
                'AutoUV processing exceeded ' + str(CONFIG['file_timeout']) +
                ' seconds during external AutoUV process; process cleanup ' +
                ('succeeded.' if cleanup['ok'] else 'failed.')
            )
        return {
            'returncode': None,
            'output_exists': os.path.isfile(output_path),
            'process_cleanup': cleanup,
            'error': (
                f'UnWrapConsole3.exe timed out after {effective_timeout:.1f} seconds; '
                f'process cleanup {"succeeded" if cleanup["ok"] else "failed"}.'
            ),
        }
    ensure_file_time('external AutoUV process')
    if completed_returncode != 0:
        unsigned_code = completed_returncode & 0xFFFFFFFF
        code_hex = f'0x{unsigned_code:08X}'
        details = (stderr or stdout or '').strip()
        error = (
            f'UnWrapConsole3.exe failed with exit code {completed_returncode} ({code_hex}): '
            f'{details[-800:]}'
        )
    else:
        error = None
    return {
        'returncode': completed_returncode,
        'output_exists': os.path.isfile(output_path),
        'error': error,
        'process_cleanup': None,
    }

def import_unwrapped_mesh(output_path):
    ensure_file_time('temporary OBJ import')
    objects_before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=output_path)
    imported_objects = [item for item in bpy.data.objects if item not in objects_before]
    imported_meshes = [item for item in imported_objects if item.type == 'MESH']
    if len(imported_meshes) != 1:
        remove_imported_objects(imported_objects)
        raise RuntimeError(f'Expected one imported mesh, got {len(imported_meshes)}.')
    return imported_objects, imported_meshes[0].data

def active_uv_layer(mesh):
    layer = mesh.uv_layers.active
    if layer is None or len(layer.data) != len(mesh.loops):
        raise RuntimeError('UnWrapConsole3.exe produced no valid UV layer.')
    return layer

def ensure_target_uv(mesh):
    target_uv = mesh.uv_layers.active
    if target_uv is None:
        target_uv = mesh.uv_layers.new(name='UVMap')
    return target_uv

def copy_uv_same_topology(source_mesh, imported_mesh, target_uv):
    imported_uv = active_uv_layer(imported_mesh)
    validate_topology(source_mesh, imported_mesh)
    for loop_index, imported_loop in enumerate(imported_uv.data):
        target_uv.data[loop_index].uv = imported_loop.uv

def normalize_target_uv(obj, target_uv, options):
    normalized = False
    if options['udims'] == 1 and options['normalize_uv']:
        normalized = normalize_uv_layer_to_single_tile(
            target_uv,
            1.0 / max(1, int(options['resolution'])),
        )
    obj.data.uv_layers.active_index = obj.data.uv_layers.find(target_uv.name)
    obj.data.update()
    return normalized

def process_mesh(obj, temp_root, index):
    ensure_file_time('mesh ' + obj.name)
    stem = f'{index:04d}_{safe_name(obj.name)}'
    original_input_path = os.path.join(temp_root, stem + '.obj')
    original_output_path = os.path.join(temp_root, stem + '_unwrapped.obj')
    original_mesh = obj.data
    target_uv = ensure_target_uv(original_mesh)

    export_selected_obj(obj, original_input_path)
    original_attempt = run_unwrap(original_input_path, original_output_path)
    if original_attempt['returncode'] is None:
        raise RuntimeError(original_attempt.get('error') or 'UnWrapConsole3.exe timed out.')
    if original_attempt['output_exists']:
        imported_objects = []
        try:
            imported_objects, imported_mesh = import_unwrapped_mesh(original_output_path)
            copy_uv_same_topology(original_mesh, imported_mesh, target_uv)
            ensure_file_time('UV transfer for ' + obj.name)
            normalized = normalize_target_uv(obj, target_uv, CONFIG['auto_uv_options'])
            return {
                'object': obj.name,
                'uv_map': target_uv.name,
                'uv_loops': len(target_uv.data),
                'returncode': original_attempt['returncode'],
                'normalized': normalized,
            }
        except FileProcessingTimeout:
            raise
        except Exception as error:
            raise RuntimeError(f'Original topology AutoUV output is invalid: {error}') from error
        finally:
            remove_imported_objects(imported_objects)
    elif original_attempt.get('error'):
        raise RuntimeError(original_attempt['error'])
    raise RuntimeError('UnWrapConsole3.exe produced no output OBJ.')

def resolve_obj_index(raw_index, count):
    index = int(raw_index)
    if index > 0:
        resolved = index - 1
    elif index < 0:
        resolved = count + index
    else:
        raise ValueError('OBJ index cannot be zero.')
    if resolved < 0 or resolved >= count:
        raise ValueError('OBJ index is out of range.')
    return resolved

def parse_obj_geometry_and_uv(path):
    vertices = []
    uvs = []
    faces = []
    with open(path, 'r', encoding='utf-8', errors='replace') as obj_file:
        for line_number, line in enumerate(obj_file, start=1):
            parts = line.strip().split()
            if not parts or parts[0].startswith('#'):
                continue
            record = parts[0]
            try:
                if record == 'v':
                    if len(parts) < 4:
                        raise ValueError('incomplete vertex')
                    vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                elif record == 'vt':
                    if len(parts) < 3:
                        raise ValueError('incomplete UV')
                    uvs.append((float(parts[1]), float(parts[2])))
                elif record == 'f':
                    if len(parts) < 4:
                        raise ValueError('face needs at least three corners')
                    corners = []
                    for token in parts[1:]:
                        fields = token.split('/')
                        vertex_index = resolve_obj_index(fields[0], len(vertices))
                        uv_index = None
                        if len(fields) > 1 and fields[1]:
                            uv_index = resolve_obj_index(fields[1], len(uvs))
                        corners.append((vertex_index, uv_index))
                    faces.append(corners)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    f'failed to parse OBJ line {line_number}: {error}'
                ) from error
    return {'vertices': vertices, 'uvs': uvs, 'faces': faces}

def float_close(left, right, epsilon=1.0e-5):
    return abs(left - right) <= epsilon

def remove_temporary_object(obj):
    if obj is None:
        return
    mesh = getattr(obj, 'data', None)
    if obj.name in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0 and mesh.name in bpy.data.meshes:
        bpy.data.meshes.remove(mesh)
    bpy.context.view_layer.update()

def create_combined_object(objects, manifest_path):
    vertices = []
    faces = []
    loop_sources = []
    mesh_entries = []
    active_uv_layers = []

    for mesh_index, obj in enumerate(objects):
        source_mesh = obj.data
        vertex_offset = len(vertices)
        polygon_offset = len(faces)
        loop_offset = len(loop_sources)
        vertices.extend(tuple(obj.matrix_world @ vertex.co) for vertex in source_mesh.vertices)
        for polygon in source_mesh.polygons:
            face = []
            for loop_index in polygon.loop_indices:
                loop = source_mesh.loops[loop_index]
                face.append(vertex_offset + loop.vertex_index)
                loop_sources.append({'mesh_index': mesh_index, 'loop_index': loop_index})
            faces.append(face)
        active_uv_layers.append(source_mesh.uv_layers.active)
        mesh_entries.append({
            'mesh_index': mesh_index,
            'object_name': obj.name,
            'mesh_name': source_mesh.name,
            'vertex_count': len(source_mesh.vertices),
            'polygon_count': len(source_mesh.polygons),
            'loop_count': len(source_mesh.loops),
            'vertex_offset': vertex_offset,
            'polygon_offset': polygon_offset,
            'loop_offset': loop_offset,
        })

    if not vertices or not faces or not loop_sources:
        raise RuntimeError('selected meshes have no faces or vertices to export')
    manifest = {
        'version': 1,
        'meshes': mesh_entries,
        'loop_sources': loop_sources,
        'vertex_count': len(vertices),
        'polygon_count': len(faces),
        'loop_count': len(loop_sources),
    }
    with open(manifest_path, 'w', encoding='utf-8') as manifest_file:
        json.dump(manifest, manifest_file, sort_keys=True)

    combined_mesh = bpy.data.meshes.new('__AutoUV_CombinedMesh')
    combined_mesh.from_pydata(vertices, [], faces)
    combined_mesh.update()
    if any(layer is not None for layer in active_uv_layers):
        combined_uv = combined_mesh.uv_layers.new(name='UVMap')
        for combined_loop_index, source in enumerate(loop_sources):
            source_layer = active_uv_layers[source['mesh_index']]
            if source_layer is not None:
                combined_uv.data[combined_loop_index].uv = source_layer.data[source['loop_index']].uv

    combined_loop_normals = []
    for obj in objects:
        try:
            normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
        except ValueError:
            normal_matrix = obj.matrix_world.to_3x3()
        for loop in obj.data.loops:
            world_normal = normal_matrix @ loop.normal
            if world_normal.length <= 1.0e-8:
                world_normal = Vector((0.0, 0.0, 1.0))
            else:
                world_normal.normalize()
            combined_loop_normals.append(tuple(world_normal))
    if len(combined_loop_normals) == len(combined_mesh.loops):
        combined_mesh.normals_split_custom_set(combined_loop_normals)
        combined_mesh.update()

    combined_object = bpy.data.objects.new('__AutoUV_CombinedObject', combined_mesh)
    collection = objects[0].users_collection[0] if objects[0].users_collection else bpy.context.scene.collection
    collection.objects.link(combined_object)
    bpy.context.view_layer.update()
    return combined_object, manifest

def validate_combined_obj_and_write_uv(objects, combined_object, input_path, output_path, manifest):
    input_data = parse_obj_geometry_and_uv(input_path)
    output_data = parse_obj_geometry_and_uv(output_path)
    combined_mesh = combined_object.data
    expected_faces = [
        [combined_mesh.loops[index].vertex_index for index in polygon.loop_indices]
        for polygon in combined_mesh.polygons
    ]
    if len(input_data['vertices']) != len(combined_mesh.vertices):
        raise RuntimeError('input OBJ vertex count does not match manifest')
    if len(input_data['faces']) != len(expected_faces):
        raise RuntimeError('input OBJ face count does not match manifest')
    if len(output_data['vertices']) != len(input_data['vertices']):
        raise RuntimeError('external OBJ changed vertex count')
    if len(output_data['faces']) != len(input_data['faces']):
        raise RuntimeError('external OBJ changed face count')
    for index, (input_vertex, output_vertex) in enumerate(zip(input_data['vertices'], output_data['vertices'])):
        if not all(float_close(input_vertex[axis], output_vertex[axis]) for axis in range(3)):
            raise RuntimeError(f'external OBJ changed vertex coordinates at vertex {index}')
    for face_index, (expected_face, input_face, output_face) in enumerate(zip(expected_faces, input_data['faces'], output_data['faces'])):
        input_vertices = [corner[0] for corner in input_face]
        output_vertices = [corner[0] for corner in output_face]
        if input_vertices != expected_face:
            raise RuntimeError(f'input OBJ reordered faces or vertices at face {face_index}')
        if output_vertices != input_vertices:
            raise RuntimeError(f'external OBJ reordered faces or vertices at face {face_index}')
        if len(output_face) != len(input_face):
            raise RuntimeError(f'external OBJ changed corner count at face {face_index}')
    if len(manifest.get('loop_sources', [])) != len(combined_mesh.loops):
        raise RuntimeError('loop mapping count mismatch')

    source_layers = [obj.data.uv_layers.active for obj in objects]
    pending_uvs = []
    seen_loops = set()
    for face_index, output_face in enumerate(output_data['faces']):
        polygon = combined_mesh.polygons[face_index]
        for corner_index, corner in enumerate(output_face):
            uv_index = corner[1]
            if uv_index is None or uv_index >= len(output_data['uvs']):
                raise RuntimeError(f'external OBJ has no valid UV index at face {face_index}')
            combined_loop_index = polygon.loop_start + corner_index
            source = manifest['loop_sources'][combined_loop_index]
            source_key = (source['mesh_index'], source['loop_index'])
            if source_key in seen_loops:
                raise RuntimeError('loop mapping contains duplicate write targets')
            seen_loops.add(source_key)
            pending_uvs.append((source['mesh_index'], source['loop_index'], output_data['uvs'][uv_index]))
    expected_loop_count = sum(len(obj.data.loops) for obj in objects)
    if len(seen_loops) != expected_loop_count:
        raise RuntimeError('external OBJ did not return UVs for every source loop')
    for mesh_index, obj in enumerate(objects):
        if source_layers[mesh_index] is None:
            source_layers[mesh_index] = obj.data.uv_layers.new(name='UVMap')
    for mesh_index, loop_index, uv in pending_uvs:
        source_layers[mesh_index].data[loop_index].uv = uv
    for mesh_index, obj in enumerate(objects):
        obj.data.uv_layers.active_index = obj.data.uv_layers.find(source_layers[mesh_index].name)
        obj.data.update()

def normalize_meshes_globally(objects, options):
    active_layers = []
    min_x = float('inf')
    min_y = float('inf')
    max_x = float('-inf')
    max_y = float('-inf')
    for obj in objects:
        uv_layer = obj.data.uv_layers.active
        if uv_layer is None or not uv_layer.data:
            raise RuntimeError(f'mesh {obj.name} has no active UV to normalize')
        active_layers.append((obj.data, uv_layer))
        for item in uv_layer.data:
            min_x = min(min_x, item.uv.x)
            min_y = min(min_y, item.uv.y)
            max_x = max(max_x, item.uv.x)
            max_y = max(max_y, item.uv.y)
    if not active_layers:
        raise RuntimeError('no active UV layers to normalize')
    largest_span = max(max_x - min_x, max_y - min_y)
    if largest_span <= 1.0e-12:
        return False
    margin = 1.0 / max(1, int(options['resolution']))
    usable_size = max(1.0e-6, 1.0 - (2.0 * margin))
    scale = usable_size / largest_span
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    for mesh, uv_layer in active_layers:
        for item in uv_layer.data:
            item.uv.x = (item.uv.x - center_x) * scale + 0.5
            item.uv.y = (item.uv.y - center_y) * scale + 0.5
        mesh.update()
    return True

def snapshot_uv_state(objects):
    snapshot = []
    for obj in objects:
        mesh = obj.data
        layers = []
        for layer in mesh.uv_layers:
            layers.append({
                'name': layer.name,
                'uvs': [(item.uv.x, item.uv.y) for item in layer.data],
            })
        snapshot.append({
            'mesh': mesh,
            'layers': layers,
            'active_index': mesh.uv_layers.active_index,
        })
    return snapshot

def restore_uv_state(snapshot):
    for item in snapshot:
        mesh = item['mesh']
        while mesh.uv_layers:
            mesh.uv_layers.remove(mesh.uv_layers[0])
        for layer_state in item['layers']:
            layer = mesh.uv_layers.new(name=layer_state['name'])
            for loop_index, uv in enumerate(layer_state['uvs']):
                if loop_index < len(layer.data):
                    layer.data[loop_index].uv = uv
        if mesh.uv_layers:
            mesh.uv_layers.active_index = min(
                item['active_index'], len(mesh.uv_layers) - 1
            )
        mesh.update()

def process_combined(objects, temp_root, index):
    ensure_file_time('combined mesh ' + str(index))
    input_path = os.path.join(temp_root, f'{index:04d}_combined_input.obj')
    output_path = os.path.join(temp_root, f'{index:04d}_combined_unwrapped.obj')
    manifest_path = os.path.join(temp_root, f'{index:04d}_combined_manifest.json')
    combined_object = None
    try:
        combined_object, manifest = create_combined_object(objects, manifest_path)
        set_only_selected(combined_object)
        bpy.ops.wm.obj_export(filepath=input_path, export_selected_objects=True, export_materials=False)
        if not os.path.isfile(input_path):
            raise RuntimeError('Blender did not produce the combined temporary OBJ.')
        attempt = run_unwrap(input_path, output_path)
        if attempt['returncode'] is None:
            raise RuntimeError(attempt.get('error') or 'UnWrapConsole3.exe timed out.')
        if not attempt['output_exists']:
            raise RuntimeError(attempt.get('error') or 'UnWrapConsole3.exe produced no combined output OBJ.')
        validate_combined_obj_and_write_uv(objects, combined_object, input_path, output_path, manifest)
        return {
            'returncode': attempt['returncode'],
            'warning': attempt.get('error') if attempt['returncode'] not in (None, 0) else None,
            'external_calls': 1,
        }
    finally:
        remove_temporary_object(combined_object)

profile = source_profile(CONFIG['fbx_path'])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(
    filepath=CONFIG['fbx_path'],
    use_manual_orientation=False,
    use_custom_normals=True,
    use_anim=True,
    use_custom_props=True,
    ignore_leaf_bones=False,
    automatic_bone_orientation=False,
    use_prepost_rot=True,
)
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
if not meshes:
    raise RuntimeError('FBX import produced no mesh objects.')
seen_data = set()
unique_meshes = []
for obj in meshes:
    data_key = obj.data.as_pointer()
    if data_key in seen_data:
        continue
    seen_data.add(data_key)
    unique_meshes.append(obj)

preflight = preflight_topology(unique_meshes)
ensure_file_time('FBX import and topology preflight')
if preflight['skipped']:
    print('FBX_AUTO_UV_PREFLIGHT=' + json.dumps(preflight, sort_keys=True), flush=True)
    # Blender's quit operator may return to the Python script in background
    # mode.  Exit here after the marker is flushed so no OBJ/FBX or external
    # Ministry of Flat process can be started for a skipped file.
    os._exit(0)

temp_root = CONFIG.get('temporary_root')
if temp_root:
    os.makedirs(temp_root, exist_ok=True)
else:
    temp_root = tempfile.mkdtemp(prefix='cli_ministry_of_flat_', dir=bpy.app.tempdir)
processed = []
warnings = []
options = CONFIG['auto_uv_options']
merge_requested = bool(options.get('merge_meshes', True))
normalize_requested = bool(options.get('normalize_uv', True))
merge_applied = bool(merge_requested and options['udims'] == 1)
normalize_applied = False
normalization_skipped_reason = None
external_call_count = 0
normalized_mesh_count = 0
uv_snapshot = snapshot_uv_state(unique_meshes)
if options['udims'] > 1:
    normalization_skipped_reason = 'udims_greater_than_one'
    if merge_requested:
        warnings.append('UDIM>1: merge disabled and UV normalization skipped.')
if options['world_scale'] and normalize_requested and options['udims'] == 1:
    warnings.append('World-scale UV is normalized to 0-1; absolute texel density may change.')
if preflight.get('risk_level') == 'medium':
    warnings.append(
        'Medium topology risk score ' + str(preflight.get('risk_score', 0)) +
        '; triggered: ' + ', '.join(preflight.get('reasons') or [])
    )
try:
    if merge_applied:
        combined_result = process_combined(unique_meshes, temp_root, 1)
        external_call_count = 1
        if combined_result.get('warning'):
            warnings.append('Combined Meshes: ' + str(combined_result['warning']))
        for obj in unique_meshes:
            target_uv = ensure_target_uv(obj.data)
            processed.append({
                'object': obj.name,
                'uv_map': target_uv.name,
                'uv_loops': len(target_uv.data),
                'normalized': False,
            })
        if normalize_requested:
            normalize_applied = normalize_meshes_globally(unique_meshes, options)
            normalized_mesh_count = len(unique_meshes) if normalize_applied else 0
        else:
            normalization_skipped_reason = 'disabled_by_option'
    else:
        for index, obj in enumerate(unique_meshes, start=1):
            result = process_mesh(obj, temp_root, index)
            processed.append(result)
            external_call_count += 1
            if result.get('returncode') not in (None, 0):
                warnings.append(f"{obj.name}: external return code {result['returncode']} with a valid output")
            if result.get('normalized'):
                normalized_mesh_count += 1
        normalize_applied = bool(normalize_requested and options['udims'] == 1 and normalized_mesh_count)
        if not normalize_requested:
            normalization_skipped_reason = 'disabled_by_option'
except FileProcessingTimeout as error:
    shutil.rmtree(temp_root, ignore_errors=True)
    if os.path.exists(CONFIG['output_path']):
        os.remove(CONFIG['output_path'])
    print('FBX_AUTO_UV_SKIP=' + json.dumps({
        'skipped': True,
        'skip_reason': 'processing_timeout',
        'timeout_seconds': CONFIG['file_timeout'],
        'error': str(error),
        'process_cleanup': LAST_PROCESS_CLEANUP,
        'preflight': preflight,
    }, sort_keys=True), flush=True)
    os._exit(0)
except Exception:
    restore_uv_state(uv_snapshot)
    if os.path.exists(CONFIG['output_path']):
        os.remove(CONFIG['output_path'])
    raise
finally:
    shutil.rmtree(temp_root, ignore_errors=True)

try:
    ensure_file_time('FBX output export')
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = profile['unit_scale_factor'] / 100.0
    bpy.ops.export_scene.fbx(
        filepath=CONFIG['output_path'],
        check_existing=False,
        use_selection=False,
        use_visible=False,
        use_active_collection=False,
        global_scale=100.0 / profile['unit_scale_factor'],
        apply_unit_scale=True,
        apply_scale_options='FBX_SCALE_UNITS',
        use_space_transform=True,
        bake_space_transform=False,
        object_types={'EMPTY', 'CAMERA', 'LIGHT', 'ARMATURE', 'MESH', 'OTHER'},
        use_mesh_modifiers=False,
        use_mesh_modifiers_render=False,
        use_subsurf=False,
        add_leaf_bones=False,
        bake_anim=True,
        bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=True,
        bake_anim_use_all_actions=True,
        bake_anim_force_startend_keying=True,
        bake_anim_step=1.0,
        bake_anim_simplify_factor=1.0,
        use_custom_props=True,
        use_metadata=True,
        path_mode='AUTO',
        embed_textures=False,
        axis_forward=profile['axis_forward'],
        axis_up=profile['axis_up'],
    )
    ensure_file_time('FBX output export')
except FileProcessingTimeout as error:
    if os.path.exists(CONFIG['output_path']):
        os.remove(CONFIG['output_path'])
    print('FBX_AUTO_UV_SKIP=' + json.dumps({
        'skipped': True,
        'skip_reason': 'processing_timeout',
        'timeout_seconds': CONFIG['file_timeout'],
        'error': str(error),
        'process_cleanup': LAST_PROCESS_CLEANUP,
        'preflight': preflight,
    }, sort_keys=True), flush=True)
    os._exit(0)
if not os.path.isfile(CONFIG['output_path']):
    raise RuntimeError('Blender produced no AutoUV output FBX.')
result = {
    'blender_version': bpy.app.version_string,
    'mesh_objects': len(meshes),
    'unique_mesh_datablocks': len(unique_meshes),
    'processed_mesh_objects': len(processed),
    'normalized_meshes': normalized_mesh_count,
    'uv_loop_count': sum(item['uv_loops'] for item in processed),
    'active_uv_maps': [item['uv_map'] for item in processed],
    'external_warnings': warnings,
    'external_call_count': external_call_count,
    'merge_meshes_requested': merge_requested,
    'merge_meshes_applied': merge_applied,
    'merge_mesh_count': len(unique_meshes) if merge_applied else 0,
    'normalize_uv_requested': normalize_requested,
    'normalize_uv_applied': normalize_applied,
    'normalization_skipped_reason': normalization_skipped_reason,
    'normalization_margin': 1.0 / max(1, int(options['resolution'])) if options['udims'] == 1 else None,
    'preflight': preflight,
    'source_profile': profile,
    'auto_uv_options': CONFIG['auto_uv_options'],
}
print('FBX_AUTO_UV_RESULT=' + json.dumps(result, sort_keys=True))
'''.replace('__CONFIG__', config)
    return script


def generate_fbx_auto_uniform_uv_script(
    fbx_path: str,
    output_path: str,
    *,
    angle_candidates: Optional[Sequence[float]] = None,
    smart_uv_options: Optional[Dict[str, object]] = None,
) -> str:
    """Generate the Blender script for angle search and uniformity scoring."""
    angles = _validate_uniform_uv_angles(angle_candidates)
    options = _validate_smart_uv_options(smart_uv_options or {})
    config = repr({
        "fbx_path": os.path.abspath(fbx_path),
        "output_path": os.path.abspath(output_path),
        "angle_candidates": angles,
        "smart_uv_options": options,
    })
    return "\n".join([
        "import bpy", "import json", "import inspect", "import math",
        "from io_scene_fbx import import_fbx, parse_fbx",
        "from io_scene_fbx.fbx_utils import RIGHT_HAND_AXES",
        f"CONFIG = {config}",
        "_light_source = inspect.getsource(import_fbx.blen_read_light)",
        "if 'lamp.cycles.cast_shadow = lamp.use_shadow' in _light_source:",
        "    _light_source = _light_source.replace('        lamp.cycles.cast_shadow = lamp.use_shadow', '        try:\\n            lamp.cycles.cast_shadow = lamp.use_shadow\\n        except AttributeError:\\n            pass')",
        "    _light_namespace = dict(import_fbx.__dict__)",
        "    exec(compile(_light_source, '<fbx_light_compat>', 'exec'), _light_namespace)",
        "    import_fbx.blen_read_light = _light_namespace['blen_read_light']",
        "def source_profile(path):",
        "    root, version = parse_fbx.parse(path)",
        "    settings = import_fbx.elem_find_first(root, b'GlobalSettings')",
        "    props = import_fbx.elem_find_first(settings, b'Properties70') if settings else None",
        "    if props is None: raise RuntimeError('FBX has no GlobalSettings/Properties70 block.')",
        "    unit = float(import_fbx.elem_props_get_number(props, b'UnitScaleFactor', 1.0))",
        "    up = (int(import_fbx.elem_props_get_integer(props, b'UpAxis', 2)), int(import_fbx.elem_props_get_integer(props, b'UpAxisSign', 1)))",
        "    forward = (int(import_fbx.elem_props_get_integer(props, b'FrontAxis', 1)), int(import_fbx.elem_props_get_integer(props, b'FrontAxisSign', 1)))",
        "    coord = (int(import_fbx.elem_props_get_integer(props, b'CoordAxis', 0)), int(import_fbx.elem_props_get_integer(props, b'CoordAxisSign', 1)))",
        "    axis_key = (up, forward, coord)",
        "    axis_map = {value: key for key, value in RIGHT_HAND_AXES.items()}",
        "    if axis_key not in axis_map: raise RuntimeError('Source FBX axis system is not representable by Blender FBX exporter: ' + repr(axis_key))",
        "    axis_up, axis_forward = axis_map[axis_key]",
        "    if unit <= 0: raise RuntimeError('Source FBX UnitScaleFactor must be positive.')",
        "    return {'axis_up': axis_up, 'axis_forward': axis_forward, 'unit_scale_factor': unit, 'version': version}",
        "def weighted_percentile(values, weights, fraction):",
        "    if not values: raise RuntimeError('UV quality analysis produced no valid triangles.')",
        "    pairs = sorted(zip(values, weights), key=lambda item: item[0])",
        "    target = sum(weights) * fraction; running = 0.0",
        "    for value, weight in pairs:",
        "        running += weight",
        "        if running >= target: return value",
        "    return pairs[-1][0]",
        "def triangle_quality(obj, uv, li0, li1, li2):",
        "    data = obj.data; indices = (li0, li1, li2)",
        "    points = [obj.matrix_world @ data.vertices[data.loops[li].vertex_index].co for li in indices]",
        "    edge1 = points[1] - points[0]; edge2 = points[2] - points[0]",
        "    area3 = edge1.cross(edge2).length * 0.5",
        "    if area3 <= 1e-12: return None",
        "    x_axis = edge1.normalized(); y_axis = edge1.cross(edge2).normalized().cross(x_axis).normalized()",
        "    x1 = edge1.length; x2 = edge2.dot(x_axis); y2 = edge2.dot(y_axis)",
        "    if abs(x1 * y2) <= 1e-12: return None",
        "    uv0, uv1, uv2 = [uv.data[li].uv for li in indices]",
        "    du1 = uv1 - uv0; du2 = uv2 - uv0",
        "    a00 = du1.x / x1; a01 = du2.x / y2 - du1.x * x2 / (x1 * y2)",
        "    a10 = du1.y / x1; a11 = du2.y / y2 - du1.y * x2 / (x1 * y2)",
        "    aa = a00 * a00 + a10 * a10; dd = a01 * a01 + a11 * a11; bb = a00 * a01 + a10 * a11",
        "    trace = aa + dd; discriminant = max((aa - dd) * (aa - dd) + 4.0 * bb * bb, 0.0)",
        "    largest = max((trace + math.sqrt(discriminant)) * 0.5, 0.0)",
        "    smallest = max((trace - math.sqrt(discriminant)) * 0.5, 0.0)",
        "    if smallest <= 1e-20 or largest <= 1e-20: return None",
        "    determinant = abs(a00 * a11 - a01 * a10)",
        "    if not all(math.isfinite(value) for value in (largest, smallest, determinant)): return None",
        "    return {'stretch': math.sqrt(largest / smallest), 'density': math.sqrt(determinant), 'area': area3}",
        "def quality_metrics():",
        "    stretch_values = []; stretch_weights = []; density_logs = []; density_weights = []; invalid = 0",
        "    for obj in unique_meshes:",
        "        uv = obj.data.uv_layers.active",
        "        if uv is None or len(uv.data) != len(obj.data.loops): raise RuntimeError('Uniform UV Project produced an invalid UV layer for ' + obj.name)",
        "        for polygon in obj.data.polygons:",
        "            loops = list(polygon.loop_indices)",
        "            for index in range(1, len(loops) - 1):",
        "                result = triangle_quality(obj, uv, loops[0], loops[index], loops[index + 1])",
        "                if result is None: invalid += 1; continue",
        "                stretch_values.append(result['stretch']); stretch_weights.append(result['area'])",
        "                density_logs.append(math.log(max(result['density'], 1e-30))); density_weights.append(result['area'])",
        "    if not stretch_values or not density_logs: raise RuntimeError('Uniform UV Project produced no measurable UV triangles.')",
        "    log_stretch = [math.log(max(value, 1.0)) for value in stretch_values]",
        "    stretch_p95 = math.exp(weighted_percentile(log_stretch, stretch_weights, 0.95))",
        "    stretch_max = max(stretch_values)",
        "    weight_total = sum(density_weights); density_mean = sum(value * weight for value, weight in zip(density_logs, density_weights)) / weight_total",
        "    density_variance = sum(weight * (value - density_mean) ** 2 for value, weight in zip(density_logs, density_weights)) / weight_total",
        "    return {'stretch_p95': stretch_p95, 'stretch_max': stretch_max, 'density_log_std': math.sqrt(max(density_variance, 0.0)), 'invalid_triangles': invalid}",
        "def unwrap(angle):",
        "    options = dict(CONFIG['smart_uv_options']); options['angle_limit'] = angle",
        "    for obj in unique_meshes:",
        "        for candidate in bpy.context.view_layer.objects: candidate.select_set(False)",
        "        obj.select_set(True); bpy.context.view_layer.objects.active = obj",
        "        if bpy.context.object and bpy.context.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')",
        "        bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')",
        "        bpy.ops.uv.smart_project(**options); bpy.ops.object.mode_set(mode='OBJECT')",
        "profile = source_profile(CONFIG['fbx_path'])",
        "bpy.ops.wm.read_factory_settings(use_empty=True)",
        "bpy.ops.import_scene.fbx(filepath=CONFIG['fbx_path'], use_manual_orientation=False, use_custom_normals=True, use_anim=True, use_custom_props=True, ignore_leaf_bones=False, automatic_bone_orientation=False, use_prepost_rot=True)",
        "meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']",
        "if not meshes: raise RuntimeError('FBX import produced no mesh objects.')",
        "seen_data = set(); unique_meshes = []",
        "for obj in meshes:",
        "    data_key = obj.data.as_pointer()",
        "    if data_key in seen_data: continue",
        "    seen_data.add(data_key); unique_meshes.append(obj)",
        "    while len(obj.data.uv_layers) > 0: obj.data.uv_layers.remove(obj.data.uv_layers[0])",
        "    obj.data.uv_layers.new(name='map1')",
        "candidates = []",
        "for angle in CONFIG['angle_candidates']:",
        "    unwrap(angle); metrics = quality_metrics()",
        "    candidates.append({'angle_limit_radians': angle, 'angle_limit_degrees': math.degrees(angle), 'metrics': metrics})",
        "valid_candidates = [item for item in candidates if item['metrics']['invalid_triangles'] == 0]",
        "if not valid_candidates: raise RuntimeError('All uniform UV candidates produced invalid UV triangles.')",
        "selected = min(valid_candidates, key=lambda item: (item['metrics']['stretch_p95'], item['metrics']['stretch_max'], item['metrics']['density_log_std'], item['angle_limit_radians']))",
        "unwrap(selected['angle_limit_radians']); final_metrics = quality_metrics()",
        "bpy.context.scene.unit_settings.system = 'METRIC'",
        "bpy.context.scene.unit_settings.scale_length = profile['unit_scale_factor'] / 100.0",
        "bpy.ops.export_scene.fbx(filepath=CONFIG['output_path'], check_existing=False, use_selection=False, use_visible=False, use_active_collection=False, global_scale=100.0 / profile['unit_scale_factor'], apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS', use_space_transform=True, bake_space_transform=False, object_types={'EMPTY', 'CAMERA', 'LIGHT', 'ARMATURE', 'MESH', 'OTHER'}, use_mesh_modifiers=False, use_mesh_modifiers_render=False, use_subsurf=False, add_leaf_bones=False, bake_anim=True, bake_anim_use_all_bones=True, bake_anim_use_nla_strips=True, bake_anim_use_all_actions=True, bake_anim_force_startend_keying=True, bake_anim_step=1.0, bake_anim_simplify_factor=1.0, use_custom_props=True, use_metadata=True, path_mode='AUTO', axis_forward=profile['axis_forward'], axis_up=profile['axis_up'])",
        "selected_options = dict(CONFIG['smart_uv_options']); selected_options['angle_limit'] = selected['angle_limit_radians']",
        "result = {'blender_version': bpy.app.version_string, 'mesh_objects': len(meshes), 'unique_mesh_datablocks': len(unique_meshes), 'uv_loop_count': sum(len(obj.data.uv_layers.active.data) for obj in unique_meshes), 'source_profile': profile, 'smart_uv_options': selected_options, 'objective': 'uniform-checker', 'candidates': candidates, 'selected_candidate': {'angle_limit_radians': selected['angle_limit_radians'], 'angle_limit_degrees': selected['angle_limit_degrees'], 'metrics': final_metrics}}",
        "print('FBX_UNIFORM_UV_RESULT=' + json.dumps(result, sort_keys=True))",
    ])


def generate_fbx_smart_uv_script(
    fbx_path: str,
    output_path: str,
    *,
    smart_uv_options: Optional[Dict[str, object]] = None,
) -> str:
    """Generate the Blender script that imports, unwraps, and exports an FBX."""
    options = _validate_smart_uv_options(smart_uv_options or {})
    config = repr({"fbx_path": os.path.abspath(fbx_path),
                   "output_path": os.path.abspath(output_path),
                   "smart_uv_options": options})
    return "\n".join([
        "import bpy", "import json", "import inspect", "from io_scene_fbx import import_fbx, parse_fbx",
        "from io_scene_fbx.fbx_utils import RIGHT_HAND_AXES", f"CONFIG = {config}",
        "_light_source = inspect.getsource(import_fbx.blen_read_light)",
        "if 'lamp.cycles.cast_shadow = lamp.use_shadow' in _light_source:",
        "    _light_source = _light_source.replace('        lamp.cycles.cast_shadow = lamp.use_shadow', '        try:\\n            lamp.cycles.cast_shadow = lamp.use_shadow\\n        except AttributeError:\\n            pass')",
        "    _light_namespace = dict(import_fbx.__dict__)",
        "    exec(compile(_light_source, '<fbx_light_compat>', 'exec'), _light_namespace)",
        "    import_fbx.blen_read_light = _light_namespace['blen_read_light']",
        "def get_prop(props, name, default):",
        "    return import_fbx.elem_props_get_number(props, name, default) if isinstance(default, float) else import_fbx.elem_props_get_integer(props, name, default)",
        "def source_profile(path):",
        "    root, version = parse_fbx.parse(path)",
        "    settings = import_fbx.elem_find_first(root, b'GlobalSettings')",
        "    props = import_fbx.elem_find_first(settings, b'Properties70') if settings else None",
        "    if props is None: raise RuntimeError('FBX has no GlobalSettings/Properties70 block.')",
        "    unit = float(import_fbx.elem_props_get_number(props, b'UnitScaleFactor', 1.0))",
        "    up = (int(import_fbx.elem_props_get_integer(props, b'UpAxis', 2)), int(import_fbx.elem_props_get_integer(props, b'UpAxisSign', 1)))",
        "    forward = (int(import_fbx.elem_props_get_integer(props, b'FrontAxis', 1)), int(import_fbx.elem_props_get_integer(props, b'FrontAxisSign', 1)))",
        "    coord = (int(import_fbx.elem_props_get_integer(props, b'CoordAxis', 0)), int(import_fbx.elem_props_get_integer(props, b'CoordAxisSign', 1)))",
        "    axis_key = (up, forward, coord)",
        "    axis_map = {value: key for key, value in RIGHT_HAND_AXES.items()}",
        "    if axis_key not in axis_map: raise RuntimeError('Source FBX axis system is not representable by Blender FBX exporter: ' + repr(axis_key))",
        "    axis_up, axis_forward = axis_map[axis_key]",
        "    if unit <= 0: raise RuntimeError('Source FBX UnitScaleFactor must be positive.')",
        "    return {'axis_up': axis_up, 'axis_forward': axis_forward, 'unit_scale_factor': unit, 'version': version}",
        "profile = source_profile(CONFIG['fbx_path'])",
        "bpy.ops.wm.read_factory_settings(use_empty=True)",
        "bpy.ops.import_scene.fbx(filepath=CONFIG['fbx_path'], use_manual_orientation=False, use_custom_normals=True, use_anim=True, use_custom_props=True, ignore_leaf_bones=False, automatic_bone_orientation=False, use_prepost_rot=True)",
        "meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']",
        "if not meshes: raise RuntimeError('FBX import produced no mesh objects.')",
        "seen_data = set()", "uv_loop_count = 0", "unique_mesh_datablocks = 0",
        "for obj in meshes:",
        "    data_key = obj.data.as_pointer()",
        "    if data_key in seen_data: continue",
        "    seen_data.add(data_key)", "    unique_mesh_datablocks += 1",
        "    for candidate in bpy.context.view_layer.objects: candidate.select_set(False)",
        "    obj.select_set(True)", "    bpy.context.view_layer.objects.active = obj",
        "    if bpy.context.object and bpy.context.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')",
        "    bpy.ops.object.mode_set(mode='EDIT')", "    bpy.ops.mesh.select_all(action='SELECT')",
        "    bpy.ops.uv.smart_project(**CONFIG['smart_uv_options'])",
        "    bpy.ops.object.mode_set(mode='OBJECT')",
        "    uv = obj.data.uv_layers.active",
        "    if uv is None or len(uv.data) != len(obj.data.loops): raise RuntimeError('Smart UV Project produced an invalid UV layer for ' + obj.name)",
        "    uv_loop_count += len(uv.data)",
        "bpy.context.scene.unit_settings.system = 'METRIC'",
        "bpy.context.scene.unit_settings.scale_length = profile['unit_scale_factor'] / 100.0",
        "bpy.ops.export_scene.fbx(filepath=CONFIG['output_path'], check_existing=False, use_selection=False, use_visible=False, use_active_collection=False, global_scale=100.0 / profile['unit_scale_factor'], apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS', use_space_transform=True, bake_space_transform=False, object_types={'EMPTY', 'CAMERA', 'LIGHT', 'ARMATURE', 'MESH', 'OTHER'}, use_mesh_modifiers=False, use_mesh_modifiers_render=False, use_subsurf=False, add_leaf_bones=False, bake_anim=True, bake_anim_use_all_bones=True, bake_anim_use_nla_strips=True, bake_anim_use_all_actions=True, bake_anim_force_startend_keying=True, bake_anim_step=1.0, bake_anim_simplify_factor=1.0, use_custom_props=True, use_metadata=True, path_mode='AUTO', embed_textures=False, axis_forward=profile['axis_forward'], axis_up=profile['axis_up'])",
        "print('FBX_SMART_UV_RESULT=' + json.dumps({'blender_version': bpy.app.version_string, 'mesh_objects': len(meshes), 'unique_mesh_datablocks': unique_mesh_datablocks, 'uv_loop_count': uv_loop_count, 'source_profile': profile}, sort_keys=True))",
    ])


def generate_fbx_smart_uv_validation_script(fbx_path: str, output_path: str) -> str:
    """Generate a Blender script that compares source/output FBX semantics."""
    config = repr({"source_fbx": os.path.abspath(fbx_path),
                   "output_fbx": os.path.abspath(output_path)})
    return "\n".join([
        "import bpy", "import json", "import inspect", "from io_scene_fbx import import_fbx, parse_fbx",
        "from io_scene_fbx.fbx_utils import RIGHT_HAND_AXES", f"CONFIG = {config}",
        "_light_source = inspect.getsource(import_fbx.blen_read_light)",
        "if 'lamp.cycles.cast_shadow = lamp.use_shadow' in _light_source:",
        "    _light_source = _light_source.replace('        lamp.cycles.cast_shadow = lamp.use_shadow', '        try:\\n            lamp.cycles.cast_shadow = lamp.use_shadow\\n        except AttributeError:\\n            pass')",
        "    _light_namespace = dict(import_fbx.__dict__)",
        "    exec(compile(_light_source, '<fbx_light_compat>', 'exec'), _light_namespace)",
        "    import_fbx.blen_read_light = _light_namespace['blen_read_light']",
        "def source_profile(path):",
        "    root, version = parse_fbx.parse(path)",
        "    settings = import_fbx.elem_find_first(root, b'GlobalSettings')",
        "    props = import_fbx.elem_find_first(settings, b'Properties70') if settings else None",
        "    if props is None: raise RuntimeError('FBX has no GlobalSettings/Properties70 block.')",
        "    unit = float(import_fbx.elem_props_get_number(props, b'UnitScaleFactor', 1.0))",
        "    up = (int(import_fbx.elem_props_get_integer(props, b'UpAxis', 2)), int(import_fbx.elem_props_get_integer(props, b'UpAxisSign', 1)))",
        "    forward = (int(import_fbx.elem_props_get_integer(props, b'FrontAxis', 1)), int(import_fbx.elem_props_get_integer(props, b'FrontAxisSign', 1)))",
        "    coord = (int(import_fbx.elem_props_get_integer(props, b'CoordAxis', 0)), int(import_fbx.elem_props_get_integer(props, b'CoordAxisSign', 1)))",
        "    axis_key = (up, forward, coord)", "    axis_map = {value: key for key, value in RIGHT_HAND_AXES.items()}",
        "    return {'axis_key': axis_key, 'axis_up': axis_map.get(axis_key, (None, None))[0], 'axis_forward': axis_map.get(axis_key, (None, None))[1], 'unit_scale_factor': unit, 'version': version}",
        "def matrix_values(matrix): return [round(float(value), 7) for row in matrix for value in row]",
        "def matrices_close(left, right): return len(left) == len(right) and all(abs(a - b) <= 1e-5 for a, b in zip(left, right))",
        "def action_signature(obj):",
        "    action = obj.animation_data.action if obj.animation_data and obj.animation_data.action else None",
        "    if action is None: return None",
        "    if hasattr(action, 'fcurves'): fcurves = list(action.fcurves)",
        "    else: fcurves = [curve for layer in action.layers for strip in layer.strips for bag in strip.channelbags for curve in bag.fcurves]",
        "    return {'fcurves': len(fcurves), 'keys': sum(len(curve.keyframe_points) for curve in fcurves), 'frame_range': [round(float(value), 6) for value in action.frame_range]}",
        "def snapshot():",
        "    result = {}",
        "    for obj in sorted(bpy.context.scene.objects, key=lambda item: item.name):",
        "        item = {'type': obj.type, 'parent': obj.parent.name if obj.parent else None, 'matrix_world': matrix_values(obj.matrix_world), 'matrix_local': matrix_values(obj.matrix_local), 'determinant_sign': 1 if obj.matrix_world.to_3x3().determinant() >= 0 else -1, 'action': action_signature(obj)}",
        "        if obj.type == 'MESH':",
        "            data = obj.data", "            item['mesh'] = {'vertices': len(data.vertices), 'polygons': len(data.polygons), 'loops': len(data.loops), 'materials': [slot.name if slot else None for slot in data.materials], 'uv_layers': [{'name': layer.name, 'count': len(layer.data)} for layer in data.uv_layers], 'uv_valid': bool(data.uv_layers and all(len(layer.data) == len(data.loops) for layer in data.uv_layers))}",
        "        if obj.type == 'ARMATURE': item['bones'] = sorted((bone.name, bone.parent.name if bone.parent else None) for bone in obj.data.bones)",
        "        result[obj.name] = item",
        "    return result",
        "def import_and_snapshot(path):",
        "    bpy.ops.wm.read_factory_settings(use_empty=True)",
        "    profile = source_profile(path)",
        "    bpy.ops.import_scene.fbx(filepath=path, use_manual_orientation=False, use_custom_normals=True, use_anim=True, use_custom_props=True, ignore_leaf_bones=False, automatic_bone_orientation=False, use_prepost_rot=True)",
        "    return profile, snapshot()",
        "source_profile_value, source = import_and_snapshot(CONFIG['source_fbx'])",
        "output_profile_value, output = import_and_snapshot(CONFIG['output_fbx'])",
        "errors = []",
        "if source_profile_value['axis_key'] != output_profile_value['axis_key']: errors.append('FBX axis system changed')",
        "if abs(source_profile_value['unit_scale_factor'] - output_profile_value['unit_scale_factor']) > 1e-6: errors.append('FBX unit scale changed')",
        "if set(source) != set(output): errors.append('object names/count changed')",
        "for name in sorted(set(source).intersection(output)):",
        "    left, right = source[name], output[name]",
        "    if (left['type'], left['parent'], left['determinant_sign']) != (right['type'], right['parent'], right['determinant_sign']): errors.append('hierarchy/handedness changed: ' + name)",
        "    if not matrices_close(left['matrix_world'], right['matrix_world']) or not matrices_close(left['matrix_local'], right['matrix_local']): errors.append('transform changed: ' + name)",
        "    if left.get('action') != right.get('action'): errors.append('animation changed: ' + name)",
        "    if left.get('bones') != right.get('bones'): errors.append('armature hierarchy changed: ' + name)",
        "    if 'mesh' in left:",
        "        if left['mesh']['vertices'] != right['mesh']['vertices'] or left['mesh']['polygons'] != right['mesh']['polygons'] or left['mesh']['loops'] != right['mesh']['loops'] or left['mesh']['materials'] != right['mesh']['materials']: errors.append('mesh structure/materials changed: ' + name)",
        "        if not right['mesh']['uv_valid'] or not right['mesh']['uv_layers']: errors.append('output mesh has no valid UV: ' + name)",
        "validation = {'ok': not errors, 'hierarchy': not any('hierarchy' in error or 'object names' in error for error in errors), 'transforms': not any('transform changed' in error for error in errors), 'handedness': not any('handedness' in error for error in errors), 'mesh_structure': not any('mesh structure' in error for error in errors), 'uv_present': not any('no valid UV' in error for error in errors), 'animation': not any('animation changed' in error for error in errors), 'errors': errors}",
        "print('FBX_SMART_UV_VALIDATION=' + json.dumps(validation, sort_keys=True))",
    ])
