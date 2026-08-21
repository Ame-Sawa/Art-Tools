from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from fbx_modifier_tool.services.fbx_service import diff_fbx_summaries, summarize_fbx_scene


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two FBX files and print structural differences.")
    parser.add_argument("left", type=Path, help="Path to the original FBX file.")
    parser.add_argument("right", type=Path, help="Path to the comparison FBX file.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the diff result as JSON.",
    )
    return parser


def format_diff_text(diff_result) -> str:
    lines: list[str] = []
    lines.append(f"Left: {diff_result.left_path}")
    lines.append(f"Right: {diff_result.right_path}")

    if diff_result.errors:
        lines.append("Errors:")
        for error in diff_result.errors:
            lines.append(f"- {error}")
        return "\n".join(lines)

    if diff_result.scene_differences:
        lines.append("Scene Differences:")
        for entry in diff_result.scene_differences:
            lines.append(f"- {entry.field_name}: {entry.left_value} -> {entry.right_value}")

    if diff_result.added_mesh_paths:
        lines.append("Added Mesh Paths:")
        for path in diff_result.added_mesh_paths:
            lines.append(f"- {path}")

    if diff_result.removed_mesh_paths:
        lines.append("Removed Mesh Paths:")
        for path in diff_result.removed_mesh_paths:
            lines.append(f"- {path}")

    if diff_result.changed_meshes:
        lines.append("Changed Meshes:")
        for mesh_diff in diff_result.changed_meshes:
            lines.append(f"- {mesh_diff.node_path}")
            for entry in mesh_diff.differences:
                lines.append(f"  {entry.field_name}: {entry.left_value} -> {entry.right_value}")

    if diff_result.diagnostics:
        lines.append("Diagnostics:")
        for diagnostic in diff_result.diagnostics:
            lines.append(f"- {diagnostic}")

    if diff_result.warnings:
        lines.append("Warnings:")
        for warning in diff_result.warnings:
            lines.append(f"- {warning}")

    if (
        not diff_result.scene_differences
        and not diff_result.added_mesh_paths
        and not diff_result.removed_mesh_paths
        and not diff_result.changed_meshes
        and not diff_result.diagnostics
        and not diff_result.warnings
    ):
        lines.append("No structural differences found.")

    return "\n".join(lines)


def _to_json_ready(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _to_json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json_ready(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    left_summary = summarize_fbx_scene(args.left)
    right_summary = summarize_fbx_scene(args.right)
    diff_result = diff_fbx_summaries(left_summary, right_summary)

    if args.json:
        print(json.dumps(_to_json_ready(asdict(diff_result)), ensure_ascii=False, indent=2))
    else:
        print(format_diff_text(diff_result))

    return 0 if diff_result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
