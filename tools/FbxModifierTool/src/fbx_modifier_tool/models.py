from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class RuntimeStatus:
    python_version: str
    pyside6_available: bool
    fbx_available: bool
    messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FbxListItem:
    file_path: Path
    relative_path: Path
    file_size: int


@dataclass(slots=True)
class FolderScanResult:
    success: bool
    folder_path: Path
    items: list[FbxListItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MeshRenameEntry:
    node_path: str
    original_name: str
    current_name: str


@dataclass(slots=True)
class MaterialRenameEntry:
    original_name: str
    current_name: str


@dataclass(slots=True)
class FbxDocument:
    success: bool
    import_path: Path
    export_path: Path | None = None
    write_smoothed_normals_to_vertex_color: bool = True
    mesh_entries: list[MeshRenameEntry] = field(default_factory=list)
    material_entries: list[MaterialRenameEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExportResult:
    success: bool
    import_path: Path
    export_path: Path
    mesh_rename_count: int = 0
    material_rename_count: int = 0
    vertex_color_mesh_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FbxMeshSummary:
    node_path: str
    node_name: str
    material_names: list[str] = field(default_factory=list)
    local_translation: tuple[float, float, float] | None = None
    local_rotation: tuple[float, float, float] | None = None
    local_scaling: tuple[float, float, float] | None = None
    geometric_translation: tuple[float, float, float] | None = None
    geometric_rotation: tuple[float, float, float] | None = None
    geometric_scaling: tuple[float, float, float] | None = None
    control_point_count: int = 0
    polygon_count: int = 0
    polygon_vertex_count: int = 0
    uv_layer_count: int = 0
    uv0_mapping_mode: str = ""
    uv0_reference_mode: str = ""
    uv0_direct_count: int = 0
    uv0_index_count: int = 0
    normal_layer_count: int = 0
    normal0_mapping_mode: str = ""
    normal0_reference_mode: str = ""
    normal0_direct_count: int = 0
    normal0_index_count: int = 0
    tangent_layer_count: int = 0
    tangent0_mapping_mode: str = ""
    tangent0_reference_mode: str = ""
    tangent0_direct_count: int = 0
    tangent0_index_count: int = 0
    binormal_layer_count: int = 0
    binormal0_mapping_mode: str = ""
    binormal0_reference_mode: str = ""
    binormal0_direct_count: int = 0
    binormal0_index_count: int = 0
    vertex_color_layer_count: int = 0
    skin_deformer_count: int = 0
    blend_shape_deformer_count: int = 0
    has_bad_polygon_size: bool = False
    bounds_min: tuple[float, float, float] | None = None
    bounds_max: tuple[float, float, float] | None = None


@dataclass(slots=True)
class FbxSceneSummary:
    success: bool
    import_path: Path
    file_size: int = 0
    node_count: int = 0
    mesh_count: int = 0
    material_count: int = 0
    mesh_summaries: list[FbxMeshSummary] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FbxDiffEntry:
    field_name: str
    left_value: object
    right_value: object


@dataclass(slots=True)
class FbxMeshDiff:
    node_path: str
    left_name: str
    right_name: str
    differences: list[FbxDiffEntry] = field(default_factory=list)


@dataclass(slots=True)
class FbxSceneDiff:
    success: bool
    left_path: Path
    right_path: Path
    scene_differences: list[FbxDiffEntry] = field(default_factory=list)
    added_mesh_paths: list[str] = field(default_factory=list)
    removed_mesh_paths: list[str] = field(default_factory=list)
    changed_meshes: list[FbxMeshDiff] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
