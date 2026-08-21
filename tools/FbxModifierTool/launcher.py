from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    tool_root = Path(__file__).resolve().parent
    src_dir = tool_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # The packaged executable, when built, lives in
    # tools/FbxModifierTool/dist/FbxModifierTool/.  The handoff module also
    # imports the sibling DccExportCommon package, so make the repository
    # Tools directory available in both source and frozen modes.
    if getattr(sys, "frozen", False):
        tools_root = Path(sys.executable).resolve().parent.parents[2]
    else:
        tools_root = tool_root.parent
    if str(tools_root) not in sys.path:
        sys.path.insert(0, str(tools_root))

    if len(sys.argv) > 1 and sys.argv[1] in {"--help", "-h"}:
        print("用法：")
        print("  launcher.py                 启动 FBX Modifier Tool GUI")
        print("  launcher.py --handoff ...   执行无界面 DCC FBX handoff")
        return 0

    # Used by Max/Maya as a silent SDK-backed postprocessor.  Keeping this in
    # the shipped EXE removes the need for each artist to create a local venv.
    if len(sys.argv) > 1 and sys.argv[1] == "--handoff":
        from fbx_modifier_tool.handoff import main as run_handoff

        return run_handoff(sys.argv[2:])

    from fbx_modifier_tool.main import main as run_main

    return run_main()


if __name__ == "__main__":
    raise SystemExit(main())
