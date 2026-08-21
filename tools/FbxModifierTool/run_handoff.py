"""Repository-local entry point for headless DCC FBX handoff processing."""

from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parent / "src"
TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from fbx_modifier_tool.handoff import main


if __name__ == "__main__":
    raise SystemExit(main())
