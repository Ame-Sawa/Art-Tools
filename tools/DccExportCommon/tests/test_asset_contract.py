from DccExportCommon.asset_contract import (
    build_export_mesh_name,
    build_working_name,
    compare_compatibility,
    make_contract,
)


def test_new_asset_contract_uses_short_delivery_names():
    contract = make_contract("Well_01.fbx", ["Mesh_Main", "Mesh_Rope"], ["Main", "Rope"])
    assert contract["is_valid"]
    assert contract["asset_name"] == "Well_01"
    assert build_working_name("Well_01", "Mesh_Main") == "Well_01__Mesh_Main"


def test_direct_update_rejects_material_slot_rename():
    contract = make_contract("Well_01", ["Mesh_Main"], ["Main", "Metal"])
    result = compare_compatibility(contract, ["Mesh_Main"], ["Main", "MetalPaint"])
    assert not result["is_compatible"]
    assert any("Metal" in issue for issue in result["issues"])


def test_direct_update_rejects_recorded_hierarchy_and_skeleton_changes():
    contract = make_contract(
        "Hero",
        ["Mesh_Body"],
        ["Body"],
        node_paths=["Root[0]", "Root[0]/Hips[0]"],
        skeleton_paths=["Root[0]/Hips[0]"],
    )
    result = compare_compatibility(
        contract,
        ["Mesh_Body"],
        ["Body"],
        current_node_paths=["Root[0]", "Root[0]/Pelvis[0]"],
        current_skeleton_paths=["Root[0]/Pelvis[0]"],
    )
    assert not result["is_compatible"]
    assert any("Root[0]/Hips[0]" in issue for issue in result["issues"])


def test_mesh_lod_name_is_preserved():
    assert build_export_mesh_name("Body_LOD1") == "Mesh_Body_LOD1"
