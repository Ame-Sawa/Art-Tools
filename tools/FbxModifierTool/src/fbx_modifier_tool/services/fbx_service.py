from __future__ import annotations

import platform
from collections import defaultdict
from pathlib import Path
from typing import Any

from fbx_modifier_tool.models import (
    ExportResult,
    FbxDocument,
    FbxDiffEntry,
    FbxListItem,
    FbxMeshDiff,
    FbxMeshSummary,
    FbxSceneDiff,
    FbxSceneSummary,
    FolderScanResult,
    MaterialRenameEntry,
    MeshRenameEntry,
    RuntimeStatus,
)

MESH_PREFIX = "Mesh_"
OUTLINE_VERTEX_COLOR_LAYER_NAME = "OutlineSmoothedNormal"


def detect_runtime_status() -> RuntimeStatus:
    messages: list[str] = []

    try:
        import PySide6  # noqa: F401

        pyside6_available = True
    except Exception as exc:
        pyside6_available = False
        messages.append(f"PySide6 不可用：{exc}")

    try:
        import fbx  # type: ignore  # noqa: F401

        fbx_available = True
    except Exception as exc:
        fbx_available = False
        messages.append(f"FBX SDK 不可用：{exc}")

    return RuntimeStatus(
        python_version=platform.python_version(),
        pyside6_available=pyside6_available,
        fbx_available=fbx_available,
        messages=messages,
    )


def ensure_mesh_prefix(name: str) -> str:
    """Normalize a user-entered Mesh name with the canonical prefix."""
    stripped = (name or "").strip()
    if not stripped:
        return MESH_PREFIX
    if stripped.startswith(MESH_PREFIX):
        return stripped
    return f"{MESH_PREFIX}{stripped}"


def normalize_material_name(name: str) -> str:
    return (name or "").strip()


def build_default_export_path(import_path: Path) -> Path:
    import_path = import_path.expanduser().resolve()
    return import_path


def _import_fbx_module() -> Any:
    import fbx  # type: ignore

    return fbx


def _load_scene(file_path: Path) -> tuple[Any, Any, Any]:
    fbx = _import_fbx_module()

    manager = fbx.FbxManager.Create()
    if manager is None:
        raise RuntimeError("FbxManager.Create() 返回空。")

    ios = fbx.FbxIOSettings.Create(manager, fbx.IOSROOT)
    manager.SetIOSettings(ios)

    importer = fbx.FbxImporter.Create(manager, "")
    if not importer.Initialize(str(file_path), -1, manager.GetIOSettings()):
        message = importer.GetStatus().GetErrorString() if hasattr(importer, "GetStatus") else ""
        importer.Destroy()
        manager.Destroy()
        raise RuntimeError(f"FBX 导入初始化失败：{file_path} {message}".strip())

    scene = fbx.FbxScene.Create(manager, "Scene")
    if not importer.Import(scene):
        message = importer.GetStatus().GetErrorString() if hasattr(importer, "GetStatus") else ""
        importer.Destroy()
        manager.Destroy()
        raise RuntimeError(f"FBX 导入失败：{file_path} {message}".strip())

    importer.Destroy()
    return fbx, manager, scene


def _is_mesh_node(node_attribute: Any, fbx_module: Any | None = None) -> bool:
    if node_attribute is None:
        return False

    try:
        attribute_type = node_attribute.GetAttributeType()
    except Exception:
        return False

    if fbx_module is not None:
        try:
            if attribute_type == fbx_module.FbxNodeAttribute.EType.eMesh:
                return True
        except Exception:
            pass

    try:
        return int(attribute_type) == 4
    except Exception:
        return False


def _is_skeleton_node(node_attribute: Any, fbx_module: Any | None = None) -> bool:
    if node_attribute is None:
        return False
    try:
        attribute_type = node_attribute.GetAttributeType()
        if fbx_module is not None:
            return attribute_type == fbx_module.FbxNodeAttribute.EType.eSkeleton
        return int(attribute_type) == 3
    except Exception:
        return False


def capture_fbx_identity(import_path: Path) -> dict[str, list[str]]:
    """Return the strict identity fields used before replacing a formal FBX."""
    fbx_module, manager, scene = _load_scene(import_path)
    try:
        node_paths: list[str] = []
        skeleton_paths: list[str] = []
        root_node = scene.GetRootNode()
        if root_node is not None:
            def visit(node: Any, parent_path: str) -> None:
                sibling_counts: dict[str, int] = {}
                for index in range(int(node.GetChildCount())):
                    child = node.GetChild(index)
                    name = str(child.GetName() or "").strip()
                    sibling_index = sibling_counts.get(name, 0)
                    sibling_counts[name] = sibling_index + 1
                    path = _build_node_path(parent_path, name, sibling_index)
                    node_paths.append(path)
                    if _is_skeleton_node(child.GetNodeAttribute(), fbx_module):
                        skeleton_paths.append(path)
                    visit(child, path)
            visit(root_node, "")
        document = load_fbx_document(import_path)
        if not document.success:
            raise RuntimeError("；".join(document.errors))
        return {
            "node_paths": node_paths,
            "skeleton_paths": skeleton_paths,
            "mesh_names": [entry.original_name for entry in document.mesh_entries],
            "material_slot_names": [entry.original_name for entry in document.material_entries],
        }
    finally:
        manager.Destroy()

def _build_node_path(parent_path: str, node_name: str, sibling_index: int) -> str:
    segment = f"{node_name or '<empty>'}[{sibling_index}]"
    if not parent_path:
        return segment
    return f"{parent_path}/{segment}"


def _collect_mesh_entries(scene: Any) -> list[MeshRenameEntry]:
    fbx_module = None
    try:
        fbx_module = _import_fbx_module()
    except Exception:
        fbx_module = None

    root_node = scene.GetRootNode()
    if root_node is None:
        return []

    entries: list[MeshRenameEntry] = []

    def visit(node: Any, parent_path: str) -> None:
        if node is None:
            return

        child_count = int(node.GetChildCount())
        sibling_name_counts: dict[str, int] = {}

        for child_index in range(child_count):
            child = node.GetChild(child_index)
            child_name = str(child.GetName() or "").strip()
            sibling_index = sibling_name_counts.get(child_name, 0)
            sibling_name_counts[child_name] = sibling_index + 1
            child_path = _build_node_path(parent_path, child_name, sibling_index)

            node_attribute = child.GetNodeAttribute()
            if _is_mesh_node(node_attribute, fbx_module):
                entries.append(
                    MeshRenameEntry(
                        node_path=child_path,
                        original_name=child_name,
                        current_name=child_name,
                    )
                )

            visit(child, child_path)

    visit(root_node, "")
    return entries


def _collect_material_entries(scene: Any) -> list[MaterialRenameEntry]:
    fbx_module = None
    try:
        fbx_module = _import_fbx_module()
    except Exception:
        fbx_module = None

    root_node = scene.GetRootNode() if hasattr(scene, "GetRootNode") else None
    if root_node is None:
        return []

    ordered_material_names: list[str] = []
    seen_material_names: set[str] = set()

    def append_material_name(material_name: str) -> None:
        normalized_name = str(material_name or "").strip()
        if not normalized_name or normalized_name in seen_material_names:
            return
        seen_material_names.add(normalized_name)
        ordered_material_names.append(normalized_name)

    def collect_from_node_material_slots(node: Any, slot_indices: list[int] | None = None) -> None:
        if not hasattr(node, "GetMaterialCount") or not hasattr(node, "GetMaterial"):
            return

        material_count = int(node.GetMaterialCount())
        if material_count <= 0:
            return

        if slot_indices is None:
            indices_to_collect = range(material_count)
        else:
            indices_to_collect = [index for index in slot_indices if 0 <= index < material_count]

        for material_index in indices_to_collect:
            material = node.GetMaterial(material_index)
            if material is None:
                continue
            append_material_name(material.GetName())

    def collect_used_material_slots(node: Any) -> bool:
        if not hasattr(node, "GetMesh"):
            return False

        mesh = node.GetMesh()
        if mesh is None or not hasattr(mesh, "GetElementMaterialCount"):
            return False

        used_slot_indices: set[int] = set()
        for element_index in range(int(mesh.GetElementMaterialCount())):
            element_material = mesh.GetElementMaterial(element_index)
            if element_material is None or not hasattr(element_material, "GetIndexArray"):
                continue

            index_array = element_material.GetIndexArray()
            if index_array is None or not hasattr(index_array, "GetCount"):
                continue

            for index_position in range(int(index_array.GetCount())):
                try:
                    used_slot_index = int(index_array.GetAt(index_position))
                except Exception:
                    continue
                if used_slot_index >= 0:
                    used_slot_indices.add(used_slot_index)

        if not used_slot_indices:
            return False

        collect_from_node_material_slots(node, sorted(used_slot_indices))
        return True

    def visit(node: Any) -> None:
        if node is None:
            return

        node_attribute = node.GetNodeAttribute() if hasattr(node, "GetNodeAttribute") else None
        if _is_mesh_node(node_attribute, fbx_module):
            if not collect_used_material_slots(node):
                collect_from_node_material_slots(node)

        if not hasattr(node, "GetChildCount") or not hasattr(node, "GetChild"):
            return

        for child_index in range(int(node.GetChildCount())):
            visit(node.GetChild(child_index))

    visit(root_node)

    return [
        MaterialRenameEntry(
            original_name=material_name,
            current_name=material_name,
        )
        for material_name in ordered_material_names
    ]


def load_fbx_document(import_path: Path, export_path: Path | None = None) -> FbxDocument:
    import_path = import_path.expanduser().resolve()
    export_path = export_path.expanduser().resolve() if export_path else None

    document = FbxDocument(success=False, import_path=import_path, export_path=export_path)

    if not import_path.exists():
        document.errors.append(f"导入文件不存在：{import_path}")
        return document

    if not import_path.is_file():
        document.errors.append(f"导入路径不是文件：{import_path}")
        return document

    try:
        _, manager, scene = _load_scene(import_path)
        try:
            document.mesh_entries = _collect_mesh_entries(scene)
            document.material_entries = _collect_material_entries(scene)
            if not document.mesh_entries:
                document.warnings.append("当前 FBX 中没有读取到任何 Mesh 节点。")
            if not document.material_entries:
                document.warnings.append("当前 FBX 中没有读取到任何材质。")
            document.success = True
        finally:
            manager.Destroy()
    except Exception as exc:
        document.errors.append(str(exc))

    return document


def summarize_fbx_scene(import_path: Path) -> FbxSceneSummary:
    import_path = import_path.expanduser().resolve()
    summary = FbxSceneSummary(success=False, import_path=import_path)

    if not import_path.exists():
        summary.errors.append(f"FBX 文件不存在：{import_path}")
        return summary

    if not import_path.is_file():
        summary.errors.append(f"FBX 路径不是文件：{import_path}")
        return summary

    summary.file_size = import_path.stat().st_size

    try:
        fbx_module, manager, scene = _load_scene(import_path)
        try:
            summary.material_count = _get_scene_material_count(scene)
            summary.mesh_summaries = _collect_mesh_summaries(scene, fbx_module)
            summary.mesh_count = len(summary.mesh_summaries)
            summary.node_count = _count_scene_nodes(scene)
            summary.success = True
            if not summary.mesh_summaries:
                summary.warnings.append("当前 FBX 中没有检测到任何 Mesh。")
        finally:
            manager.Destroy()
    except Exception as exc:
        summary.errors.append(str(exc))

    return summary


def diff_fbx_summaries(left: FbxSceneSummary, right: FbxSceneSummary) -> FbxSceneDiff:
    diff = FbxSceneDiff(
        success=False,
        left_path=left.import_path,
        right_path=right.import_path,
    )

    if not left.success:
        diff.errors.extend(f"左侧文件读取失败：{error}" for error in left.errors)
    if not right.success:
        diff.errors.extend(f"右侧文件读取失败：{error}" for error in right.errors)
    if diff.errors:
        return diff

    left_map = {mesh.node_path: mesh for mesh in left.mesh_summaries}
    right_map = {mesh.node_path: mesh for mesh in right.mesh_summaries}

    for field_name in ("file_size", "node_count", "mesh_count", "material_count"):
        left_value = getattr(left, field_name)
        right_value = getattr(right, field_name)
        if left_value != right_value:
            diff.scene_differences.append(
                FbxDiffEntry(
                    field_name=field_name,
                    left_value=left_value,
                    right_value=right_value,
                )
            )

    diff.added_mesh_paths = sorted(path for path in right_map if path not in left_map)
    diff.removed_mesh_paths = sorted(path for path in left_map if path not in right_map)

    common_paths = sorted(path for path in left_map if path in right_map)
    for path in common_paths:
        mesh_diff = _diff_mesh_summary(left_map[path], right_map[path])
        if mesh_diff.differences:
            diff.changed_meshes.append(mesh_diff)

    diff.diagnostics.extend(_build_diff_diagnostics(left, right))
    diff.warnings.extend(left.warnings)
    diff.warnings.extend(right.warnings)
    diff.success = True
    return diff


def build_smoothed_normal_vertex_colors(
    position_normal_samples: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> list[tuple[float, float, float, float]]:
    grouped_normals: dict[tuple[float, float, float], list[tuple[float, float, float]]] = defaultdict(list)
    for position, normal in position_normal_samples:
        grouped_normals[position].append(normal)

    averaged_lookup: dict[tuple[float, float, float], tuple[float, float, float]] = {}
    for position, normals in grouped_normals.items():
        averaged_lookup[position] = _normalize_vector3(
            (
                sum(normal[0] for normal in normals),
                sum(normal[1] for normal in normals),
                sum(normal[2] for normal in normals),
            )
        )

    colors: list[tuple[float, float, float, float]] = []
    for position, _normal in position_normal_samples:
        smooth_normal = averaged_lookup[position]
        colors.append(
            (
                smooth_normal[0] * 0.5 + 0.5,
                smooth_normal[1] * 0.5 + 0.5,
                smooth_normal[2] * 0.5 + 0.5,
                1.0,
            )
        )

    return colors


def scan_fbx_folder(folder_path: Path) -> FolderScanResult:
    folder_path = folder_path.expanduser().resolve()
    result = FolderScanResult(success=False, folder_path=folder_path)

    if not folder_path.exists():
        result.errors.append(f"扫描文件夹不存在：{folder_path}")
        return result

    if not folder_path.is_dir():
        result.errors.append(f"扫描路径不是文件夹：{folder_path}")
        return result

    files = sorted(
        [
            item
            for item in folder_path.iterdir()
            if item.is_file() and item.suffix.lower() == ".fbx"
        ],
        key=lambda item: item.name.lower(),
    )
    for file_path in files:
        if not file_path.is_file():
            continue
        result.items.append(
            FbxListItem(
                file_path=file_path,
                relative_path=file_path.relative_to(folder_path),
                file_size=file_path.stat().st_size,
            )
        )

    if not result.items:
        result.warnings.append("当前文件夹下没有找到任何 FBX 文件。")

    result.success = True
    return result


def _get_scene_material_count(scene: Any) -> int:
    if hasattr(scene, "GetMaterialCount"):
        try:
            return int(scene.GetMaterialCount())
        except Exception:
            return 0
    return 0


def _count_scene_nodes(scene: Any) -> int:
    root_node = scene.GetRootNode() if hasattr(scene, "GetRootNode") else None
    if root_node is None:
        return 0

    count = 0

    def visit(node: Any) -> None:
        nonlocal count
        if node is None:
            return
        count += 1
        if not hasattr(node, "GetChildCount") or not hasattr(node, "GetChild"):
            return
        for child_index in range(int(node.GetChildCount())):
            visit(node.GetChild(child_index))

    visit(root_node)
    return count


def _collect_mesh_summaries(scene: Any, fbx_module: Any) -> list[FbxMeshSummary]:
    root_node = scene.GetRootNode() if hasattr(scene, "GetRootNode") else None
    if root_node is None:
        return []

    entries: list[FbxMeshSummary] = []

    def visit(node: Any, parent_path: str) -> None:
        if node is None:
            return

        child_count = int(node.GetChildCount())
        sibling_name_counts: dict[str, int] = {}

        for child_index in range(child_count):
            child = node.GetChild(child_index)
            child_name = str(child.GetName() or "").strip()
            sibling_index = sibling_name_counts.get(child_name, 0)
            sibling_name_counts[child_name] = sibling_index + 1
            child_path = _build_node_path(parent_path, child_name, sibling_index)

            node_attribute = child.GetNodeAttribute() if hasattr(child, "GetNodeAttribute") else None
            if _is_mesh_node(node_attribute, fbx_module):
                entries.append(_build_mesh_summary(child, child_path, node_attribute, fbx_module))

            visit(child, child_path)

    visit(root_node, "")
    return entries


def _build_mesh_summary(node: Any, node_path: str, mesh: Any, fbx_module: Any) -> FbxMeshSummary:
    summary = FbxMeshSummary(
        node_path=node_path,
        node_name=str(node.GetName() or "").strip(),
        material_names=_collect_node_material_names(node),
        local_translation=_safe_property_vector3(node, "LclTranslation"),
        local_rotation=_safe_property_vector3(node, "LclRotation"),
        local_scaling=_safe_property_vector3(node, "LclScaling"),
        geometric_translation=_safe_node_geometric_vector3(node, fbx_module, "GetGeometricTranslation"),
        geometric_rotation=_safe_node_geometric_vector3(node, fbx_module, "GetGeometricRotation"),
        geometric_scaling=_safe_node_geometric_vector3(node, fbx_module, "GetGeometricScaling"),
        control_point_count=_safe_int_call(mesh, "GetControlPointsCount"),
        polygon_count=_safe_int_call(mesh, "GetPolygonCount"),
        uv_layer_count=_safe_int_call(mesh, "GetElementUVCount"),
        normal_layer_count=_safe_int_call(mesh, "GetElementNormalCount"),
        tangent_layer_count=_safe_int_call(mesh, "GetElementTangentCount"),
        binormal_layer_count=_safe_int_call(mesh, "GetElementBinormalCount"),
        vertex_color_layer_count=_safe_int_call(mesh, "GetElementVertexColorCount"),
        skin_deformer_count=_safe_get_deformer_count(mesh, fbx_module, "eSkin"),
        blend_shape_deformer_count=_safe_get_deformer_count(mesh, fbx_module, "eBlendShape"),
    )

    polygon_vertex_count = 0
    has_bad_polygon_size = False
    polygon_count = summary.polygon_count
    for polygon_index in range(polygon_count):
        try:
            polygon_size = int(mesh.GetPolygonSize(polygon_index))
        except Exception:
            continue
        polygon_vertex_count += polygon_size
        if polygon_size < 3:
            has_bad_polygon_size = True

    summary.polygon_vertex_count = polygon_vertex_count
    summary.has_bad_polygon_size = has_bad_polygon_size
    summary.bounds_min, summary.bounds_max = _compute_mesh_bounds(mesh)
    _fill_layer_summary(summary, mesh, "uv", 0)
    _fill_layer_summary(summary, mesh, "normal", 0)
    _fill_layer_summary(summary, mesh, "tangent", 0)
    _fill_layer_summary(summary, mesh, "binormal", 0)
    return summary


def _collect_node_material_names(node: Any) -> list[str]:
    if not hasattr(node, "GetMaterialCount") or not hasattr(node, "GetMaterial"):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for material_index in range(int(node.GetMaterialCount())):
        material = node.GetMaterial(material_index)
        if material is None:
            continue
        name = str(material.GetName() or "").strip()
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _compute_mesh_bounds(mesh: Any) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None]:
    point_count = _safe_int_call(mesh, "GetControlPointsCount")
    if point_count <= 0 or not hasattr(mesh, "GetControlPointAt"):
        return None, None

    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    valid = False

    for point_index in range(point_count):
        try:
            point = mesh.GetControlPointAt(point_index)
            x, y, z = _vector3_tuple(point)
        except Exception:
            continue
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        min_z = min(min_z, z)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
        max_z = max(max_z, z)
        valid = True

    if not valid:
        return None, None

    return (min_x, min_y, min_z), (max_x, max_y, max_z)


def _safe_int_call(obj: Any, method_name: str) -> int:
    if not hasattr(obj, method_name):
        return 0
    try:
        return int(getattr(obj, method_name)())
    except Exception:
        return 0


def _safe_get_deformer_count(mesh: Any, fbx_module: Any, deformer_name: str) -> int:
    if not hasattr(mesh, "GetDeformerCount"):
        return 0
    try:
        deformer_type = getattr(fbx_module.FbxDeformer.EDeformerType, deformer_name)
    except Exception:
        return 0
    try:
        return int(mesh.GetDeformerCount(deformer_type))
    except Exception:
        return 0


def _safe_property_vector3(node: Any, property_name: str) -> tuple[float, float, float] | None:
    if not hasattr(node, property_name):
        return None
    try:
        value = getattr(node, property_name).Get()
        return _vector3_tuple(value)
    except Exception:
        return None


def _safe_node_geometric_vector3(node: Any, fbx_module: Any, method_name: str) -> tuple[float, float, float] | None:
    if not hasattr(node, method_name):
        return None
    try:
        pivot = fbx_module.FbxNode.EPivotSet.eSourcePivot
        value = getattr(node, method_name)(pivot)
        return _vector3_tuple(value)
    except Exception:
        return None


def _fill_layer_summary(summary: FbxMeshSummary, mesh: Any, layer_kind: str, element_index: int) -> None:
    getter_names = {
        "uv": "GetElementUV",
        "normal": "GetElementNormal",
        "tangent": "GetElementTangent",
        "binormal": "GetElementBinormal",
    }
    getter_name = getter_names[layer_kind]
    if not hasattr(mesh, getter_name):
        return

    try:
        element = getattr(mesh, getter_name)(element_index)
    except Exception:
        element = None
    if element is None:
        return

    setattr(summary, f"{layer_kind}0_mapping_mode", _safe_enum_name(element, "GetMappingMode"))
    setattr(summary, f"{layer_kind}0_reference_mode", _safe_enum_name(element, "GetReferenceMode"))
    setattr(summary, f"{layer_kind}0_direct_count", _safe_array_count(element, "GetDirectArray"))
    setattr(summary, f"{layer_kind}0_index_count", _safe_array_count(element, "GetIndexArray"))


def _safe_enum_name(element: Any, method_name: str) -> str:
    if not hasattr(element, method_name):
        return ""
    try:
        return str(getattr(element, method_name)())
    except Exception:
        return ""


def _safe_array_count(element: Any, array_getter_name: str) -> int:
    if not hasattr(element, array_getter_name):
        return 0
    try:
        array = getattr(element, array_getter_name)()
    except Exception:
        return 0
    if array is None or not hasattr(array, "GetCount"):
        return 0
    try:
        return int(array.GetCount())
    except Exception:
        return 0


def _diff_mesh_summary(left: FbxMeshSummary, right: FbxMeshSummary) -> FbxMeshDiff:
    mesh_diff = FbxMeshDiff(
        node_path=left.node_path,
        left_name=left.node_name,
        right_name=right.node_name,
    )

    compare_fields = (
        "node_name",
        "material_names",
        "local_translation",
        "local_rotation",
        "local_scaling",
        "geometric_translation",
        "geometric_rotation",
        "geometric_scaling",
        "control_point_count",
        "polygon_count",
        "polygon_vertex_count",
        "uv_layer_count",
        "uv0_mapping_mode",
        "uv0_reference_mode",
        "uv0_direct_count",
        "uv0_index_count",
        "normal_layer_count",
        "normal0_mapping_mode",
        "normal0_reference_mode",
        "normal0_direct_count",
        "normal0_index_count",
        "tangent_layer_count",
        "tangent0_mapping_mode",
        "tangent0_reference_mode",
        "tangent0_direct_count",
        "tangent0_index_count",
        "binormal_layer_count",
        "binormal0_mapping_mode",
        "binormal0_reference_mode",
        "binormal0_direct_count",
        "binormal0_index_count",
        "vertex_color_layer_count",
        "skin_deformer_count",
        "blend_shape_deformer_count",
        "has_bad_polygon_size",
        "bounds_min",
        "bounds_max",
    )

    for field_name in compare_fields:
        left_value = getattr(left, field_name)
        right_value = getattr(right, field_name)
        if left_value != right_value:
            mesh_diff.differences.append(
                FbxDiffEntry(
                    field_name=field_name,
                    left_value=left_value,
                    right_value=right_value,
                )
            )

    return mesh_diff


def _build_diff_diagnostics(left: FbxSceneSummary, right: FbxSceneSummary) -> list[str]:
    diagnostics: list[str] = []

    if right.file_size and left.file_size > right.file_size * 1.5:
        diagnostics.append(
            "左侧文件明显更大，优先检查法线/UV layer 是否使用了更冗余的 Direct 存储，或是否带有未烘平的节点补偿变换。"
        )

    left_map = {mesh.node_path: mesh for mesh in left.mesh_summaries}
    right_map = {mesh.node_path: mesh for mesh in right.mesh_summaries}
    for path in sorted(set(left_map).intersection(right_map)):
        left_mesh = left_map[path]
        right_mesh = right_map[path]

        if (
            _has_non_zero_rotation(left_mesh.local_rotation)
            or _has_non_zero_rotation(left_mesh.geometric_rotation)
        ) and not (
            _has_non_zero_rotation(right_mesh.local_rotation)
            or _has_non_zero_rotation(right_mesh.geometric_rotation)
        ):
            diagnostics.append(
                f"{path}: 左侧存在非零 local/geometric rotation，而右侧基本被烘平；这类隐藏旋转补偿可能让 Unity 重导入或 unwrap 更不稳定。"
            )

        if (
            left_mesh.normal0_reference_mode == "EReferenceMode.eDirect"
            and right_mesh.normal0_reference_mode == "EReferenceMode.eIndexToDirect"
            and left_mesh.normal0_direct_count > right_mesh.normal0_direct_count
        ):
            diagnostics.append(
                f"{path}: 法线从 Direct 存储变为 IndexToDirect，说明右侧做了法线去重复用；这通常会显著减小文件体积。"
            )

        if (
            left_mesh.uv0_reference_mode == "EReferenceMode.eDirect"
            and right_mesh.uv0_reference_mode == "EReferenceMode.eIndexToDirect"
            and left_mesh.uv0_direct_count > right_mesh.uv0_direct_count
        ):
            diagnostics.append(
                f"{path}: UV 从 Direct 存储变为 IndexToDirect，右侧比左侧更紧凑。"
            )

        if (
            left_mesh.control_point_count == right_mesh.control_point_count
            and left_mesh.polygon_count == right_mesh.polygon_count
            and left_mesh.bounds_min != right_mesh.bounds_min
            and left_mesh.bounds_max != right_mesh.bounds_max
        ):
            diagnostics.append(
                f"{path}: 几何规模相同但包围盒轴向分布变化，可能发生了轴系烘平、节点旋转折叠，或几何被重新写回到不同局部空间。"
            )

    return diagnostics


def _has_non_zero_rotation(value: tuple[float, float, float] | None, tolerance: float = 0.001) -> bool:
    if value is None:
        return False
    return any(abs(component) > tolerance for component in value)


def rename_mesh_entry(document: FbxDocument, row_index: int, new_name: str) -> MeshRenameEntry:
    if row_index < 0 or row_index >= len(document.mesh_entries):
        raise IndexError("Mesh 行索引越界。")

    normalized_name = ensure_mesh_prefix(new_name)
    document.mesh_entries[row_index].current_name = normalized_name
    return document.mesh_entries[row_index]


def rename_material_entry(document: FbxDocument, row_index: int, new_name: str) -> MaterialRenameEntry:
    if row_index < 0 or row_index >= len(document.material_entries):
        raise IndexError("Material 行索引越界。")

    normalized_name = normalize_material_name(new_name)
    if not normalized_name:
        raise ValueError("Material 名称不能为空。")

    document.material_entries[row_index].current_name = normalized_name
    return document.material_entries[row_index]


def export_fbx_document(
    document: FbxDocument,
    export_path: Path,
    overwrite: bool = False,
    strip_root_prefix: str | list[str] | tuple[str, ...] = "",
    restore_transform_source_path: str | Path = "",
    export_animation: bool = True,
) -> ExportResult:
    import_path = document.import_path.expanduser().resolve()
    export_path = export_path.expanduser().resolve()
    result = ExportResult(success=False, import_path=import_path, export_path=export_path)

    if not document.success:
        result.errors.append("当前没有可导出的 FBX 文档。")
        return result

    if not export_path.suffix.lower() == ".fbx":
        result.errors.append(f"导出路径必须是 .fbx 文件：{export_path}")
        return result

    if export_path.exists() and not overwrite:
        result.errors.append(f"导出文件已存在：{export_path}")
        return result

    export_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        fbx, manager, scene = _load_scene(import_path)
        try:
            mesh_rename_count = _apply_mesh_renames(scene, document.mesh_entries, result)
            material_rename_count = _apply_material_renames(scene, document.material_entries)
            _strip_transient_root_nodes(scene, strip_root_prefix, result)
            _restore_node_local_transforms(
                scene,
                restore_transform_source_path,
                fbx,
                result,
            )
            vertex_color_mesh_count = 0
            if document.write_smoothed_normals_to_vertex_color:
                vertex_color_mesh_count = _apply_smoothed_normals_to_vertex_colors(scene, fbx, result)

            _set_export_animation_enabled(manager.GetIOSettings(), fbx, export_animation)

            exporter = fbx.FbxExporter.Create(manager, "")
            if not exporter.Initialize(str(export_path), -1, manager.GetIOSettings()):
                message = exporter.GetStatus().GetErrorString() if hasattr(exporter, "GetStatus") else ""
                exporter.Destroy()
                raise RuntimeError(f"FBX 导出初始化失败：{export_path} {message}".strip())

            if not exporter.Export(scene):
                message = exporter.GetStatus().GetErrorString() if hasattr(exporter, "GetStatus") else ""
                exporter.Destroy()
                raise RuntimeError(f"FBX 导出失败：{export_path} {message}".strip())

            exporter.Destroy()
        finally:
            manager.Destroy()
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    result.success = True
    result.mesh_rename_count = mesh_rename_count
    result.material_rename_count = material_rename_count
    result.vertex_color_mesh_count = vertex_color_mesh_count
    return result


def _set_export_animation_enabled(io_settings: Any, fbx_module: Any, enabled: bool) -> None:
    """Control animation serialization in the SDK handoff, outside Max."""
    property_name = getattr(fbx_module, "EXP_FBX_ANIMATION", "EXP_FBX_ANIMATION")
    if hasattr(io_settings, "SetBoolProp"):
        io_settings.SetBoolProp(property_name, bool(enabled))


def _apply_mesh_renames(scene: Any, mesh_entries: list[MeshRenameEntry], result: ExportResult) -> int:
    root_node = scene.GetRootNode()
    if root_node is None:
        return 0

    rename_map = {
        entry.node_path: entry.current_name
        for entry in mesh_entries
        if entry.current_name != entry.original_name
    }
    if not rename_map:
        return 0

    renamed_count = 0

    def visit(node: Any, parent_path: str) -> None:
        nonlocal renamed_count

        child_count = int(node.GetChildCount())
        sibling_name_counts: dict[str, int] = {}

        for child_index in range(child_count):
            child = node.GetChild(child_index)
            child_name = str(child.GetName() or "").strip()
            sibling_index = sibling_name_counts.get(child_name, 0)
            sibling_name_counts[child_name] = sibling_index + 1
            child_path = _build_node_path(parent_path, child_name, sibling_index)

            if child_path in rename_map:
                child.SetName(rename_map[child_path])
                renamed_count += 1

            visit(child, child_path)

    visit(root_node, "")

    if renamed_count != len(rename_map):
        result.warnings.append(
            f"计划重命名 {len(rename_map)} 个 Mesh，实际命中 {renamed_count} 个。"
        )

    return renamed_count


def _strip_transient_root_nodes(scene: Any, root_prefix: str | list[str] | tuple[str, ...], result: ExportResult) -> None:
    """Remove DCC-only identity groups while retaining their exported children."""
    prefixes = [root_prefix] if isinstance(root_prefix, str) else list(root_prefix or [])
    prefixes = [str(prefix or "").strip() for prefix in prefixes if str(prefix or "").strip()]
    if not prefixes:
        return
    root = scene.GetRootNode() if hasattr(scene, "GetRootNode") else None
    if root is None:
        return

    transient_nodes = [
        root.GetChild(index)
        for index in range(int(root.GetChildCount()))
        if any(str(root.GetChild(index).GetName() or "").startswith(prefix) for prefix in prefixes)
    ]
    for transient_node in transient_nodes:
        children = [transient_node.GetChild(index) for index in range(int(transient_node.GetChildCount()))]
        for child in children:
            transient_node.RemoveChild(child)
            root.AddChild(child)
        root.RemoveChild(transient_node)
        result.warnings.append("已移除 DCC 临时父节点：{0}".format(transient_node.GetName()))


def _capture_node_local_transforms(scene: Any) -> dict[str, tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]]:
    """Capture local TRS by strict FBX node path, excluding the synthetic scene root."""
    root = scene.GetRootNode() if hasattr(scene, "GetRootNode") else None
    if root is None:
        return {}

    transforms = {}

    def visit(node: Any, parent_path: str) -> None:
        sibling_counts: dict[str, int] = {}
        for index in range(int(node.GetChildCount())):
            child = node.GetChild(index)
            name = str(child.GetName() or "").strip()
            sibling_index = sibling_counts.get(name, 0)
            sibling_counts[name] = sibling_index + 1
            path = _build_node_path(parent_path, name, sibling_index)
            transforms[path] = (
                _vector3_tuple(child.LclTranslation.Get()),
                _vector3_tuple(child.LclRotation.Get()),
                _vector3_tuple(child.LclScaling.Get()),
            )
            visit(child, path)

    visit(root, "")
    return transforms


def _capture_node_rotation_pivots(
    scene: Any,
) -> dict[str, tuple[tuple[float, float, float], tuple[float, float, float], bool]]:
    """Capture FBX pivot rotation state that Unity applies in addition to TRS."""
    root = scene.GetRootNode() if hasattr(scene, "GetRootNode") else None
    if root is None:
        return {}

    pivots = {}

    def visit(node: Any, parent_path: str) -> None:
        sibling_counts: dict[str, int] = {}
        for index in range(int(node.GetChildCount())):
            child = node.GetChild(index)
            name = str(child.GetName() or "").strip()
            sibling_index = sibling_counts.get(name, 0)
            sibling_counts[name] = sibling_index + 1
            path = _build_node_path(parent_path, name, sibling_index)
            pivots[path] = (
                _vector3_tuple(child.PreRotation.Get()),
                _vector3_tuple(child.PostRotation.Get()),
                bool(child.GetRotationActive()),
            )
            visit(child, path)

    visit(root, "")
    return pivots


def _restore_node_local_transforms(
    scene: Any,
    reference_path: str | Path,
    fbx_module: Any,
    result: ExportResult,
) -> None:
    """Restore linked static-asset transforms after Max's FBX axis bridge.

    Max imports Y-up FBX into its Z-up scene by placing conversion values on
    the imported root nodes.  Exporting selected objects can retain those
    values even when the outgoing FBX is marked Y-up.  The linked source FBX
    is the authoritative transform contract, so restore matching local TRS
    values after transient DCC containers have been removed.
    """
    if not reference_path:
        return
    reference = Path(reference_path).expanduser().resolve()
    if not reference.is_file():
        result.warnings.append("未恢复原 FBX 节点变换：引用文件不存在：{0}".format(reference))
        return

    reference_manager = None
    try:
        _, reference_manager, reference_scene = _load_scene(reference)
        reference_transforms = _capture_node_local_transforms(reference_scene)
        reference_rotation_pivots = _capture_node_rotation_pivots(reference_scene)
        if not reference_transforms:
            result.warnings.append("原 FBX 不含可恢复的节点变换：{0}".format(reference.name))
            return

        restored_count = 0
        root = scene.GetRootNode() if hasattr(scene, "GetRootNode") else None
        if root is None:
            return

        def visit(node: Any, parent_path: str) -> None:
            nonlocal restored_count
            sibling_counts: dict[str, int] = {}
            for index in range(int(node.GetChildCount())):
                child = node.GetChild(index)
                name = str(child.GetName() or "").strip()
                sibling_index = sibling_counts.get(name, 0)
                sibling_counts[name] = sibling_index + 1
                path = _build_node_path(parent_path, name, sibling_index)
                transform = reference_transforms.get(path)
                if transform is not None:
                    translation, rotation, scaling = transform
                    child.LclTranslation.Set(fbx_module.FbxDouble3(*translation))
                    child.LclRotation.Set(fbx_module.FbxDouble3(*rotation))
                    child.LclScaling.Set(fbx_module.FbxDouble3(*scaling))
                    pre_rotation, post_rotation, rotation_active = reference_rotation_pivots.get(
                        path,
                        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), child.GetRotationActive()),
                    )
                    pivot_set = fbx_module.FbxNode.EPivotSet.eSourcePivot
                    child.SetPreRotation(pivot_set, fbx_module.FbxVector4(*pre_rotation))
                    child.SetPostRotation(pivot_set, fbx_module.FbxVector4(*post_rotation))
                    child.SetRotationActive(bool(rotation_active))
                    restored_count += 1
                visit(child, path)

        visit(root, "")
        if restored_count:
            result.warnings.append("已按原 FBX 恢复 {0} 个节点的局部变换。".format(restored_count))
        else:
            result.warnings.append("未找到可与原 FBX 匹配的节点，未恢复局部变换。")
    except Exception as exc:
        result.warnings.append("恢复原 FBX 节点变换失败：{0}".format(exc))
    finally:
        if reference_manager is not None:
            reference_manager.Destroy()


def _apply_material_renames(scene: Any, material_entries: list[MaterialRenameEntry]) -> int:
    if not hasattr(scene, "GetMaterialCount") or not hasattr(scene, "GetMaterial"):
        return 0

    rename_map = {
        entry.original_name: entry.current_name
        for entry in material_entries
        if entry.current_name != entry.original_name
    }
    if not rename_map:
        return 0

    renamed_count = 0
    for material_index in range(int(scene.GetMaterialCount())):
        material = scene.GetMaterial(material_index)
        if material is None:
            continue

        material_name = str(material.GetName() or "").strip()
        if material_name in rename_map:
            material.SetName(rename_map[material_name])
            renamed_count += 1

    return renamed_count


def _apply_smoothed_normals_to_vertex_colors(scene: Any, fbx_module: Any, result: ExportResult) -> int:
    root_node = scene.GetRootNode()
    if root_node is None:
        return 0

    updated_mesh_count = 0

    def visit(node: Any) -> None:
        nonlocal updated_mesh_count

        if node is None:
            return

        node_attribute = node.GetNodeAttribute() if hasattr(node, "GetNodeAttribute") else None
        if _is_mesh_node(node_attribute, fbx_module):
            mesh = node_attribute
            if _write_smoothed_normals_to_vertex_color(mesh, fbx_module):
                updated_mesh_count += 1
            else:
                node_name = str(node.GetName() or "").strip() if hasattr(node, "GetName") else "<unknown>"
                result.warnings.append(f"Mesh 未写入描边平滑法线顶点色：{node_name}")

        child_count = int(node.GetChildCount()) if hasattr(node, "GetChildCount") else 0
        for child_index in range(child_count):
            visit(node.GetChild(child_index))

    visit(root_node)
    return updated_mesh_count


def _write_smoothed_normals_to_vertex_color(mesh: Any, fbx_module: Any) -> bool:
    samples = _collect_mesh_position_normal_samples(mesh, fbx_module)
    if not samples:
        return False

    colors = build_smoothed_normal_vertex_colors(samples)
    vertex_color_element = _get_or_create_vertex_color_element(mesh, fbx_module)
    if vertex_color_element is None:
        return False

    direct_array = vertex_color_element.GetDirectArray()
    if hasattr(direct_array, "Clear"):
        direct_array.Clear()

    for red, green, blue, alpha in colors:
        direct_array.Add(fbx_module.FbxColor(red, green, blue, alpha))

    vertex_color_element.SetMappingMode(fbx_module.FbxLayerElement.EMappingMode.eByPolygonVertex)
    vertex_color_element.SetReferenceMode(fbx_module.FbxLayerElement.EReferenceMode.eDirect)
    return True


def _collect_mesh_position_normal_samples(
    mesh: Any,
    fbx_module: Any,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    if mesh is None or not hasattr(mesh, "GetPolygonCount"):
        return []

    samples: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    polygon_count = int(mesh.GetPolygonCount())
    for polygon_index in range(polygon_count):
        polygon_size = int(mesh.GetPolygonSize(polygon_index))
        for vertex_index in range(polygon_size):
            control_point_index = int(mesh.GetPolygonVertex(polygon_index, vertex_index))
            control_point = mesh.GetControlPointAt(control_point_index)
            normal = fbx_module.FbxVector4()
            if not mesh.GetPolygonVertexNormal(polygon_index, vertex_index, normal):
                continue

            samples.append(
                (
                    _vector3_key(control_point),
                    _normalize_vector3(_vector3_tuple(normal)),
                )
            )

    return samples


def _get_or_create_vertex_color_element(mesh: Any, fbx_module: Any) -> Any | None:
    existing_count = int(mesh.GetElementVertexColorCount()) if hasattr(mesh, "GetElementVertexColorCount") else 0
    for element_index in range(existing_count):
        element = mesh.GetElementVertexColor(element_index)
        if element is None:
            continue

        if not hasattr(element, "GetName") or str(element.GetName() or "") == OUTLINE_VERTEX_COLOR_LAYER_NAME:
            return element

    if hasattr(mesh, "CreateElementVertexColor"):
        element = mesh.CreateElementVertexColor(OUTLINE_VERTEX_COLOR_LAYER_NAME)
    else:
        element = fbx_module.FbxLayerElementVertexColor.Create(mesh, OUTLINE_VERTEX_COLOR_LAYER_NAME)

    if element is None:
        return None

    layer = mesh.GetLayer(0) if hasattr(mesh, "GetLayer") else None
    if layer is None and hasattr(mesh, "CreateLayer"):
        mesh.CreateLayer()
        layer = mesh.GetLayer(0)

    if layer is not None and hasattr(layer, "SetVertexColors"):
        layer.SetVertexColors(element)

    return element


def _vector3_key(value: Any) -> tuple[float, float, float]:
    vector = _vector3_tuple(value)
    return (round(vector[0], 6), round(vector[1], 6), round(vector[2], 6))


def _vector3_tuple(value: Any) -> tuple[float, float, float]:
    return (float(value[0]), float(value[1]), float(value[2]))


def _normalize_vector3(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length_squared = vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]
    if length_squared <= 1e-12:
        return (0.0, 0.0, 1.0)

    length = length_squared ** 0.5
    return (vector[0] / length, vector[1] / length, vector[2] / length)
