from __future__ import annotations

from pathlib import Path

from fbx_modifier_tool.models import FbxDocument, MaterialRenameEntry, MeshRenameEntry
from fbx_modifier_tool.models import FbxMeshSummary, FbxSceneSummary
from fbx_modifier_tool.services import fbx_service
from fbx_modifier_tool.services.fbx_service import (
    build_smoothed_normal_vertex_colors,
    build_default_export_path,
    diff_fbx_summaries,
    ensure_mesh_prefix,
    export_fbx_document,
    load_fbx_document,
    rename_material_entry,
    rename_mesh_entry,
    scan_fbx_folder,
    summarize_fbx_scene,
)


class _FakeAttribute:
    def __init__(self, attribute_type: int) -> None:
        self._attribute_type = attribute_type

    def GetAttributeType(self) -> int:
        return self._attribute_type


class _FakeMaterial:
    def __init__(self, name: str) -> None:
        self._name = name

    def GetName(self) -> str:
        return self._name

    def SetName(self, name: str) -> None:
        self._name = name


class _FakeNode:
    def __init__(self, name: str, children=None, is_mesh: bool = False, mesh=None, materials=None) -> None:
        self._name = name
        self._children = list(children or [])
        self._attribute = mesh if mesh is not None else (_FakeAttribute(4) if is_mesh else None)
        self._materials = list(materials or [])

    def GetName(self) -> str:
        return self._name

    def SetName(self, name: str) -> None:
        self._name = name

    def GetChildCount(self) -> int:
        return len(self._children)

    def GetChild(self, index: int):
        return self._children[index]

    def GetNodeAttribute(self):
        return self._attribute

    def GetMaterialCount(self) -> int:
        return len(self._materials)

    def GetMaterial(self, index: int):
        return self._materials[index]

    def GetMesh(self):
        return self._attribute if isinstance(self._attribute, _FakeMesh) else None


class _FakeScene:
    def __init__(self, root_node: _FakeNode, materials: list[_FakeMaterial]) -> None:
        self._root_node = root_node
        self._materials = materials

    def GetRootNode(self):
        return self._root_node

    def GetMaterialCount(self) -> int:
        return len(self._materials)

    def GetMaterial(self, index: int):
        return self._materials[index]


class _FakeManager:
    def Destroy(self) -> None:
        return None

    def GetIOSettings(self):
        return object()


class _FakeVector4:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.values = [x, y, z, 0.0]

    def __getitem__(self, index: int) -> float:
        return self.values[index]

    def __setitem__(self, index: int, value: float) -> None:
        self.values[index] = value


class _FakeFbxColor:
    def __init__(self, red: float, green: float, blue: float, alpha: float) -> None:
        self.values = (red, green, blue, alpha)


class _FakeDirectArray:
    def __init__(self) -> None:
        self.values = []

    def Clear(self) -> None:
        self.values.clear()

    def Add(self, value) -> None:
        self.values.append(value)


class _FakeVertexColorElement:
    def __init__(self, name: str) -> None:
        self._name = name
        self.mapping_mode = None
        self.reference_mode = None
        self._direct_array = _FakeDirectArray()

    def GetName(self) -> str:
        return self._name

    def GetDirectArray(self) -> _FakeDirectArray:
        return self._direct_array

    def SetMappingMode(self, mode) -> None:
        self.mapping_mode = mode

    def SetReferenceMode(self, mode) -> None:
        self.reference_mode = mode


class _FakeLayer:
    def __init__(self) -> None:
        self.vertex_colors = None

    def SetVertexColors(self, element) -> None:
        self.vertex_colors = element


class _FakeIndexArray:
    def __init__(self, values) -> None:
        self._values = list(values)

    def GetCount(self) -> int:
        return len(self._values)

    def GetAt(self, index: int) -> int:
        return self._values[index]


class _FakeMaterialElement:
    def __init__(self, values) -> None:
        self._index_array = _FakeIndexArray(values)

    def GetIndexArray(self):
        return self._index_array


class _FakeMesh(_FakeAttribute):
    def __init__(self, control_points, polygon_vertices, polygon_vertex_normals, material_indices=None) -> None:
        super().__init__(4)
        self._control_points = control_points
        self._polygon_vertices = polygon_vertices
        self._polygon_vertex_normals = polygon_vertex_normals
        self._vertex_color_elements = []
        self._layer = _FakeLayer()
        self._material_elements = []
        if material_indices is not None:
            self._material_elements.append(_FakeMaterialElement(material_indices))

    def GetPolygonCount(self) -> int:
        return len(self._polygon_vertices)

    def GetPolygonSize(self, polygon_index: int) -> int:
        return len(self._polygon_vertices[polygon_index])

    def GetPolygonVertex(self, polygon_index: int, vertex_index: int) -> int:
        return self._polygon_vertices[polygon_index][vertex_index]

    def GetControlPointAt(self, index: int):
        return self._control_points[index]

    def GetPolygonVertexNormal(self, polygon_index: int, vertex_index: int, vector) -> bool:
        normal = self._polygon_vertex_normals[polygon_index][vertex_index]
        vector[0] = normal[0]
        vector[1] = normal[1]
        vector[2] = normal[2]
        return True

    def GetElementVertexColorCount(self) -> int:
        return len(self._vertex_color_elements)

    def GetElementVertexColor(self, index: int):
        return self._vertex_color_elements[index]

    def CreateElementVertexColor(self, name: str):
        element = _FakeVertexColorElement(name)
        self._vertex_color_elements.append(element)
        return element

    def GetLayer(self, index: int):
        return self._layer if index == 0 else None

    def CreateLayer(self) -> None:
        self._layer = _FakeLayer()

    def GetElementMaterialCount(self) -> int:
        return len(self._material_elements)

    def GetElementMaterial(self, index: int):
        return self._material_elements[index]

    def GetControlPointsCount(self) -> int:
        return len(self._control_points)

    def GetElementUVCount(self) -> int:
        return 1

    def GetElementNormalCount(self) -> int:
        return 1

    def GetElementTangentCount(self) -> int:
        return 0

    def GetElementBinormalCount(self) -> int:
        return 0

    def GetDeformerCount(self, _deformer_type) -> int:
        return 0


def test_ensure_mesh_prefix_uses_a_single_canonical_prefix() -> None:
    assert ensure_mesh_prefix("") == "Mesh_"
    assert ensure_mesh_prefix("Body") == "Mesh_Body"
    assert ensure_mesh_prefix("Mesh_Arm") == "Mesh_Arm"


def test_build_default_export_path_appends_modified_suffix(tmp_path: Path) -> None:
    import_path = tmp_path / "SM_Tree.fbx"
    expected = tmp_path / "SM_Tree.fbx"
    assert build_default_export_path(import_path) == expected.resolve()


def test_scan_fbx_folder_lists_only_direct_fbx_files(tmp_path: Path) -> None:
    folder = tmp_path / "incoming"
    folder.mkdir()
    (folder / "a.fbx").write_text("dummy", encoding="utf-8")
    (folder / "b.FBX").write_text("dummy", encoding="utf-8")
    (folder / "readme.txt").write_text("dummy", encoding="utf-8")
    child = folder / "nested"
    child.mkdir()
    (child / "c.fbx").write_text("dummy", encoding="utf-8")

    result = scan_fbx_folder(folder)

    assert result.success is True
    assert [item.file_path.name for item in result.items] == ["a.fbx", "b.FBX"]


def test_build_smoothed_normal_vertex_colors_averages_normals_by_position() -> None:
    colors = build_smoothed_normal_vertex_colors(
        [
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        ]
    )

    shared_color = colors[0]
    assert colors[1] == shared_color
    assert shared_color[0] > 0.5
    assert shared_color[1] > 0.5
    assert shared_color[2] == 0.5
    assert colors[2] == (0.5, 0.5, 1.0, 1.0)


def test_load_fbx_document_collects_meshes_and_materials(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "tree.fbx"
    file_path.write_text("dummy", encoding="utf-8")

    root = _FakeNode(
        "Root",
        children=[
            _FakeNode("Mesh_Trunk", is_mesh=True),
            _FakeNode("Group", children=[_FakeNode("Mesh_Leaf", is_mesh=True)]),
        ],
    )
    scene = _FakeScene(root, [_FakeMaterial("Mat_Bark"), _FakeMaterial("Mat_Leaf")])

    monkeypatch.setattr(fbx_service, "_load_scene", lambda _path: (object(), _FakeManager(), scene))

    document = load_fbx_document(file_path)

    assert document.success is True
    assert [entry.original_name for entry in document.mesh_entries] == ["Mesh_Trunk", "Mesh_Leaf"]
    assert document.material_entries == []


def test_load_fbx_document_collects_only_materials_used_by_meshes(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "streetlight.fbx"
    file_path.write_text("dummy", encoding="utf-8")

    materials = [
        _FakeMaterial("Mat_Town_Building_Medium_Wall_01"),
        _FakeMaterial("Mat_ExteriorWall"),
        _FakeMaterial("Mat_TrainStation_Roof_01"),
        _FakeMaterial("Mat_TrainStation_Platform_01"),
        _FakeMaterial("Mat_TrainStation_Platform_02"),
        _FakeMaterial("Mat_Town_Building_Medium_Roof_01"),
        _FakeMaterial("Mat_MilitaryCamp"),
        _FakeMaterial("Prop_Streetlight_Steel_01"),
        _FakeMaterial("RockWall"),
    ]
    mesh = _FakeMesh(
        control_points=[(0.0, 0.0, 0.0)],
        polygon_vertices=[[0], [0], [0]],
        polygon_vertex_normals=[[(0.0, 1.0, 0.0)], [(0.0, 1.0, 0.0)], [(0.0, 1.0, 0.0)]],
        material_indices=[7, 6, 2],
    )
    root = _FakeNode(
        "Root",
        children=[_FakeNode("SM_Prop_Streetlight_02", mesh=mesh, materials=materials)],
    )
    scene = _FakeScene(root, materials)

    monkeypatch.setattr(fbx_service, "_load_scene", lambda _path: (object(), _FakeManager(), scene))

    document = load_fbx_document(file_path)

    assert document.success is True
    assert [entry.original_name for entry in document.material_entries] == [
        "Mat_TrainStation_Roof_01",
        "Mat_MilitaryCamp",
        "Prop_Streetlight_Steel_01",
    ]


def test_rename_mesh_entry_updates_current_name() -> None:
    document = FbxDocument(
        success=True,
        import_path=Path("SM_Prop_Tree.fbx"),
        mesh_entries=[MeshRenameEntry(node_path="Root[0]", original_name="Mesh_Old", current_name="Mesh_Old")],
    )

    entry = rename_mesh_entry(document, 0, "New")

    assert entry.current_name == "Mesh_New"


def test_rename_material_entry_rejects_empty_name() -> None:
    document = FbxDocument(
        success=True,
        import_path=Path("demo.fbx"),
        material_entries=[MaterialRenameEntry(original_name="Mat_Old", current_name="Mat_Old")],
    )

    try:
        rename_material_entry(document, 0, "   ")
    except ValueError as exc:
        assert "不能为空" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_export_fbx_document_applies_mesh_and_material_renames(monkeypatch, tmp_path: Path) -> None:
    input_file = tmp_path / "source.fbx"
    output_file = tmp_path / "result.fbx"
    input_file.write_text("dummy", encoding="utf-8")

    mesh = _FakeMesh(
        control_points=[
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
        ],
        polygon_vertices=[[0, 1, 2]],
        polygon_vertex_normals=[[(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]],
    )
    mesh_node = _FakeNode("Mesh_Old", mesh=mesh)
    root = _FakeNode("Root", children=[mesh_node])
    material = _FakeMaterial("Mat_Old")
    scene = _FakeScene(root, [material])

    exported_paths: list[str] = []

    class _FakeExporter:
        def Initialize(self, path: str, _index: int, _settings) -> bool:
            self._path = path
            return True

        def Export(self, _scene) -> bool:
            exported_paths.append(self._path)
            return True

        def Destroy(self) -> None:
            return None

    class _FakeExporterFactory:
        @staticmethod
        def Create(_manager, _name: str):
            return _FakeExporter()

    class _FakeFbxModule:
        FbxExporter = _FakeExporterFactory
        FbxVector4 = _FakeVector4
        FbxColor = _FakeFbxColor

        class FbxLayerElement:
            class EMappingMode:
                eByPolygonVertex = "ByPolygonVertex"

            class EReferenceMode:
                eDirect = "Direct"

    monkeypatch.setattr(fbx_service, "_load_scene", lambda _path: (_FakeFbxModule(), _FakeManager(), scene))

    document = FbxDocument(
        success=True,
        import_path=input_file,
        mesh_entries=[
            MeshRenameEntry(
                node_path="Mesh_Old[0]",
                original_name="Mesh_Old",
                current_name="Mesh_New",
            )
        ],
        material_entries=[
            MaterialRenameEntry(
                original_name="Mat_Old",
                current_name="Mat_New",
            )
        ],
    )

    result = export_fbx_document(document, output_file, overwrite=True)

    assert result.success is True
    assert result.mesh_rename_count == 1
    assert result.material_rename_count == 1
    assert result.vertex_color_mesh_count == 1
    assert mesh_node.GetName() == "Mesh_New"
    assert material.GetName() == "Mat_New"
    assert exported_paths == [str(output_file)]
    vertex_color_element = mesh.GetElementVertexColor(0)
    assert vertex_color_element.GetName() == "OutlineSmoothedNormal"
    assert vertex_color_element.mapping_mode == "ByPolygonVertex"
    assert vertex_color_element.reference_mode == "Direct"
    assert len(vertex_color_element.GetDirectArray().values) == 3


def test_summarize_and_diff_fbx_scene_reports_mesh_stat_changes(monkeypatch, tmp_path: Path) -> None:
    left_path = tmp_path / "left.fbx"
    right_path = tmp_path / "right.fbx"
    left_path.write_text("left", encoding="utf-8")
    right_path.write_text("right file content", encoding="utf-8")

    left_mesh = _FakeMesh(
        control_points=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        polygon_vertices=[[0, 1, 2]],
        polygon_vertex_normals=[[(0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0)]],
    )
    right_mesh = _FakeMesh(
        control_points=[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 2.0)],
        polygon_vertices=[[0, 1, 2], [0, 2, 3]],
        polygon_vertex_normals=[
            [(0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0)],
            [(0.0, 1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
        ],
    )
    left_scene = _FakeScene(_FakeNode("Root", children=[_FakeNode("Mesh_A", mesh=left_mesh)]), [])
    right_scene = _FakeScene(_FakeNode("Root", children=[_FakeNode("Mesh_A", mesh=right_mesh)]), [])

    def fake_load_scene(path: Path):
        class _FakeFbxModule:
            class FbxDeformer:
                class EDeformerType:
                    eSkin = "skin"
                    eBlendShape = "blend"

        if path == left_path:
            return _FakeFbxModule(), _FakeManager(), left_scene
        return _FakeFbxModule(), _FakeManager(), right_scene

    monkeypatch.setattr(fbx_service, "_load_scene", fake_load_scene)

    left_summary = summarize_fbx_scene(left_path)
    right_summary = summarize_fbx_scene(right_path)
    diff = diff_fbx_summaries(left_summary, right_summary)

    assert left_summary.success is True
    assert right_summary.success is True
    assert diff.success is True
    assert any(entry.field_name == "file_size" for entry in diff.scene_differences)
    assert len(diff.changed_meshes) == 1
    changed_fields = {entry.field_name for entry in diff.changed_meshes[0].differences}
    assert "control_point_count" in changed_fields
    assert "polygon_count" in changed_fields
    assert "bounds_max" in changed_fields


def test_diff_fbx_summaries_emits_high_signal_diagnostics() -> None:
    left_summary = FbxSceneSummary(
        success=True,
        import_path=Path("left.fbx"),
        file_size=300,
        mesh_summaries=[
            FbxMeshSummary(
                node_path="Mesh_Main[0]",
                node_name="Mesh_Main",
                local_rotation=(90.0, 0.0, 0.0),
                geometric_rotation=(-90.0, 0.0, 0.0),
                control_point_count=10,
                polygon_count=5,
                normal0_reference_mode="EReferenceMode.eDirect",
                normal0_direct_count=30,
                bounds_min=(0.0, 0.0, 0.0),
                bounds_max=(1.0, 2.0, 3.0),
            )
        ],
    )
    right_summary = FbxSceneSummary(
        success=True,
        import_path=Path("right.fbx"),
        file_size=100,
        mesh_summaries=[
            FbxMeshSummary(
                node_path="Mesh_Main[0]",
                node_name="Mesh_Main",
                local_rotation=(0.0, 0.0, 0.0),
                geometric_rotation=(0.0, 0.0, 0.0),
                control_point_count=10,
                polygon_count=5,
                normal0_reference_mode="EReferenceMode.eIndexToDirect",
                normal0_direct_count=10,
                bounds_min=(0.0, 0.0, 0.0),
                bounds_max=(1.0, 3.0, 2.0),
            )
        ],
    )

    diff = diff_fbx_summaries(left_summary, right_summary)

    assert diff.success is True
    assert any("文件明显更大" in item for item in diff.diagnostics)
    assert any("隐藏旋转补偿" in item for item in diff.diagnostics)
    assert any("法线从 Direct 存储变为 IndexToDirect" in item for item in diff.diagnostics)
