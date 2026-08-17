"""Headless FBX import, UV processing, export, and still-render helpers."""

import json
import os
import tempfile
from typing import Dict, Iterable, Optional


MULTI_ANGLE_VIEWS = ("front", "back", "left", "right", "top", "bottom", "perspective")
DEFAULT_MULTI_ANGLE_VIEWS = ("front", "right", "top", "perspective")

SMART_UV_MARGIN_METHODS = ("SCALED", "ADD", "FRACTION")
SMART_UV_ROTATE_METHODS = ("AXIS_ALIGNED", "AXIS_ALIGNED_X", "AXIS_ALIGNED_Y")
SMART_UV_DEFAULTS = {
    "angle_limit": 1.1519173383712769,
    "margin_method": "SCALED",
    "rotate_method": "AXIS_ALIGNED_Y",
    "island_margin": 0.0,
    "area_weight": 0.0,
    "correct_aspect": True,
    "scale_to_bounds": False,
}


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


def _run_blender_script(script: str, timeout: int, operation: str) -> Dict[str, object]:
    from cli_anything.blender.utils.blender_backend import run_blender_script

    result = run_blender_script(script, timeout=timeout)
    if result["returncode"] != 0:
        details = result.get("stderr", "") or result.get("stdout", "")
        raise RuntimeError(
            f"Blender {operation} failed (exit {result['returncode']}):\n{details[-2000:]}"
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
