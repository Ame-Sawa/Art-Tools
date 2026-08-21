"""Naming and compatibility contracts shared by the Max and Maya exporters.

This module deliberately has no DCC imports so its rules can be tested outside
3ds Max and Maya.  DCC adapters own scene inspection and temporary export
copies; this module decides whether the resulting handoff is legal.
"""

from __future__ import annotations

import re
from pathlib import Path


MESH_PREFIX = "Mesh_"
_VALID_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_LOD_SUFFIX = re.compile(r"^(?P<part>[A-Za-z][A-Za-z0-9]*)_LOD(?P<level>[0-9]+)$", re.IGNORECASE)


def normalize_asset_name(value):
    """Return an extensionless FBX asset name, or an empty string."""
    value = (value or "").strip()
    if value.lower().endswith(".fbx"):
        value = value[:-4]
    return value


def normalize_part_key(value):
    """Normalize a user-entered semantic key without silently inventing one."""
    value = (value or "").strip().replace(" ", "")
    if value.startswith(MESH_PREFIX):
        value = value[len(MESH_PREFIX):]
    return value


def is_valid_part_key(value):
    """Part and material-slot keys are concise identifiers such as Body or Main."""
    value = normalize_part_key(value)
    lod_match = _LOD_SUFFIX.match(value)
    if lod_match:
        value = lod_match.group("part")
    return bool(_VALID_KEY.match(value))


def build_export_mesh_name(part_key):
    """Build the canonical outgoing mesh node name from a semantic part key."""
    part_key = normalize_part_key(part_key)
    if not is_valid_part_key(part_key):
        raise ValueError("Mesh 部件名必须是如 Body、Main 或 Body_LOD1 的英文标识。")
    return MESH_PREFIX + part_key


def build_working_name(asset_alias, canonical_name):
    """Build a readable DCC-only identity name; never use it as an FBX contract."""
    asset_alias = normalize_asset_name(asset_alias)
    canonical_name = (canonical_name or "").strip()
    if not asset_alias or not canonical_name:
        raise ValueError("资产别名和交付名均不能为空。")
    return "{0}__{1}".format(asset_alias, canonical_name)


def make_contract(asset_name, mesh_names, material_slot_names, source_path="", node_paths=None, skeleton_paths=None):
    """Create a serializable export contract from canonical FBX identifiers."""
    asset_name = normalize_asset_name(asset_name)
    errors = []
    if not asset_name:
        errors.append("FBX 文件名不能为空。")

    normalized_meshes = [str(name or "").strip() for name in mesh_names or []]
    normalized_slots = [str(name or "").strip() for name in material_slot_names or []]

    if not normalized_meshes:
        errors.append("资产至少需要一个 Mesh。")
    for mesh_name in normalized_meshes:
        if not mesh_name.startswith(MESH_PREFIX) or not is_valid_part_key(mesh_name):
            errors.append("Mesh 交付名不合法：{0}".format(mesh_name or "<空>"))
    for slot_name in normalized_slots:
        if not is_valid_part_key(slot_name):
            errors.append("材质槽交付名不合法：{0}".format(slot_name or "<空>"))

    _append_duplicate_errors(errors, normalized_meshes, "Mesh")
    _append_duplicate_errors(errors, normalized_slots, "材质槽")

    return {
        "version": 1,
        "asset_name": asset_name,
        "source_path": str(source_path or ""),
        "mesh_names": normalized_meshes,
        "material_slot_names": normalized_slots,
        "node_paths": list(node_paths or []),
        "skeleton_paths": list(skeleton_paths or []),
        "errors": errors,
        "is_valid": not errors,
    }


def make_asset_link(asset_name, source_path, mesh_mapping, material_mapping, work_root="", export_set=""):
    """Create the serializable DCC-root metadata for a linked asset."""
    source_path = str(source_path or "")
    source_file_name = Path(source_path).name if source_path else ""
    mesh_mapping = dict(mesh_mapping or {})
    material_mapping = dict(material_mapping or {})
    contract = make_contract(
        asset_name or source_file_name,
        list(mesh_mapping.values()),
        list(material_mapping.values()),
        source_path,
    )
    return {
        "version": 1,
        "asset_name": contract["asset_name"],
        "source_path": source_path,
        "source_file_name": source_file_name,
        "work_root": str(work_root or ""),
        "export_set": str(export_set or ""),
        "mesh_names": mesh_mapping,
        "material_slot_names": material_mapping,
        "contract": contract,
    }


def build_handoff_mapping(asset_link):
    """Return the mapping payload expected by the headless FBX handoff tool."""
    asset_link = asset_link or {}
    return {
        "mesh_names": dict(asset_link.get("mesh_names") or {}),
        "material_slot_names": dict(asset_link.get("material_slot_names") or {}),
    }


def compare_compatibility(expected_contract, current_mesh_names, current_material_slot_names,
                          current_node_paths=None, current_skeleton_paths=None):
    """Compare structural identifiers before directly replacing a formal FBX.

    Geometry edits are intentionally not compared.  Any node or material-slot
    identity change must go through the Unity _Incoming migration path instead
    of silently breaking importer remaps.
    """
    expected_contract = expected_contract or {}
    expected_meshes = list(expected_contract.get("mesh_names") or [])
    expected_slots = list(expected_contract.get("material_slot_names") or [])
    current_meshes = [str(name or "").strip() for name in current_mesh_names or []]
    current_slots = [str(name or "").strip() for name in current_material_slot_names or []]

    issues = []
    _compare_identifier_lists("Mesh", expected_meshes, current_meshes, issues)
    _compare_identifier_lists("材质槽", expected_slots, current_slots, issues)
    expected_nodes = list(expected_contract.get("node_paths") or [])
    expected_skeletons = list(expected_contract.get("skeleton_paths") or [])
    if expected_nodes:
        _compare_identifier_lists("层级节点", expected_nodes, list(current_node_paths or []), issues)
    if expected_skeletons:
        _compare_identifier_lists("骨骼节点", expected_skeletons, list(current_skeleton_paths or []), issues)
    return {
        "is_compatible": not issues,
        "issues": issues,
        "expected_mesh_names": expected_meshes,
        "current_mesh_names": current_meshes,
        "expected_material_slot_names": expected_slots,
        "current_material_slot_names": current_slots,
        "expected_node_paths": expected_nodes,
        "current_node_paths": list(current_node_paths or []),
        "expected_skeleton_paths": expected_skeletons,
        "current_skeleton_paths": list(current_skeleton_paths or []),
    }


def _append_duplicate_errors(errors, values, display_name):
    seen = set()
    for value in values:
        key = value.lower()
        if key in seen:
            errors.append("{0} 重复：{1}".format(display_name, value))
        seen.add(key)


def _compare_identifier_lists(display_name, expected, current, issues):
    if expected == current:
        return
    expected_set = set(expected)
    current_set = set(current)
    for value in expected:
        if value not in current_set:
            issues.append("{0} 缺失或被改名：{1}".format(display_name, value))
    for value in current:
        if value not in expected_set:
            issues.append("{0} 新增或名称不匹配：{1}".format(display_name, value))
    if len(expected) != len(current):
        issues.append("{0} 数量变化：原 {1}，当前 {2}".format(display_name, len(expected), len(current)))
