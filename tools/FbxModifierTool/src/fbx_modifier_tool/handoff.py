"""Headless FBX handoff processor used by the Max and Maya exporters.

The module intentionally runs outside either DCC process.  It lets DCC scenes
keep readable working names/namespaces while writing clean Unity handoff names
to a separate FBX file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fbx_modifier_tool.services.fbx_service import (
    capture_fbx_identity,
    export_fbx_document,
    load_fbx_document,
)
from DccExportCommon.asset_contract import compare_compatibility


def _name_candidates(value: str) -> list[str]:
    value = (value or "").strip()
    candidates = [value]
    if ":" in value:
        candidates.append(value.rsplit(":", 1)[-1])
    if "__" in value:
        candidates.append(value.rsplit("__", 1)[-1])
    return [candidate for candidate in candidates if candidate]


def _resolve_name(mapping: dict[str, str], source_name: str) -> str:
    for candidate in _name_candidates(source_name):
        if candidate in mapping:
            return mapping[candidate]
    return ""


def process_handoff(source_path: Path, output_path: Path, mapping: dict, overwrite: bool) -> dict:
    """Apply a DCC working-name-to-delivery-name mapping to an FBX copy."""
    document = load_fbx_document(source_path, output_path)
    result = {
        "success": False,
        "source_path": str(source_path),
        "output_path": str(output_path),
        "errors": list(document.errors),
        "warnings": list(document.warnings),
        "mesh_rename_count": 0,
        "material_rename_count": 0,
    }
    if not document.success:
        return result

    mesh_map = dict((mapping or {}).get("mesh_names") or {})
    material_map = dict((mapping or {}).get("material_slot_names") or {})

    for entry in document.mesh_entries:
        target_name = _resolve_name(mesh_map, entry.original_name)
        if not target_name:
            result["errors"].append("未找到 Mesh 交付名映射：{0}".format(entry.original_name))
            continue
        entry.current_name = target_name

    for entry in document.material_entries:
        target_name = _resolve_name(material_map, entry.original_name)
        if not target_name:
            result["errors"].append("未找到材质槽交付名映射：{0}".format(entry.original_name))
            continue
        entry.current_name = target_name

    if result["errors"]:
        return result

    export_result = export_fbx_document(
        document,
        output_path,
        overwrite=overwrite,
        strip_root_prefix=(mapping or {}).get("strip_root_prefix", ""),
        restore_transform_source_path=(mapping or {}).get("restore_transform_source_path", ""),
        export_animation=bool((mapping or {}).get("include_animation", True)),
    )
    result["errors"].extend(export_result.errors)
    result["warnings"].extend(export_result.warnings)
    result["mesh_rename_count"] = export_result.mesh_rename_count
    result["material_rename_count"] = export_result.material_rename_count
    result["success"] = export_result.success
    contract = dict((mapping or {}).get("contract") or {})
    if result["success"] and contract:
        identity = capture_fbx_identity(output_path)
        compatibility = compare_compatibility(
            contract,
            identity["mesh_names"],
            identity["material_slot_names"],
            identity["node_paths"],
            identity["skeleton_paths"],
        )
        result["compatibility"] = compatibility
        if not compatibility["is_compatible"]:
            result["success"] = False
            result["errors"].extend(compatibility["issues"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply DeadTrail DCC handoff naming to an FBX.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output")
    parser.add_argument("--mapping-json")
    parser.add_argument("--inspect", action="store_true", help="仅输出 FBX 的严格身份快照。")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    if args.inspect:
        try:
            print(json.dumps({"success": True, "identity": capture_fbx_identity(Path(args.source))}, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps({"success": False, "errors": [str(exc)]}, ensure_ascii=False))
            return 1

    if not args.output or not args.mapping_json:
        parser.error("普通处理模式必须提供 --output 和 --mapping-json。")

    try:
        mapping = json.loads(Path(args.mapping_json).read_text(encoding="utf-8"))
    except Exception as exc:
        payload = {"success": False, "errors": ["读取映射文件失败：{0}".format(exc)]}
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = process_handoff(Path(args.source), Path(args.output), mapping, args.overwrite)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
