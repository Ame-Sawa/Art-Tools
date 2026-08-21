"""Launch the repository-local headless FBX handoff processor."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


def _tools_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _find_python() -> str:
    tools_root = _tools_root()
    candidates = [
        tools_root / "FbxModifierTool" / ".venv" / "Scripts" / "python.exe",
        Path(os.environ.get("PYTHON_EXECUTABLE", "")),
    ]
    for candidate in candidates:
        if str(candidate) and candidate.is_file():
            return str(candidate)
    raise RuntimeError("未找到 FbxModifierTool 的 Python 环境。请先运行 Tools/FbxModifierTool/init_venv.bat。")


def _find_handoff_command() -> list[str]:
    """Return the self-contained runtime first, with a venv fallback for developers."""
    tools_root = _tools_root()
    bundled_runner = tools_root / "FbxModifierTool" / "dist" / "FbxModifierTool" / "FbxModifierTool.exe"
    if bundled_runner.is_file():
        return [str(bundled_runner), "--handoff"]
    return [_find_python(), str(tools_root / "FbxModifierTool" / "run_handoff.py")]


def process_handoff(source_path, output_path, mapping, overwrite=True):
    """Run the FBX SDK postprocessor and return its JSON result."""
    source_path = str(source_path or "")
    output_path = str(output_path or "")
    if not source_path or not output_path:
        return {"success": False, "errors": ["源 FBX 和目标 FBX 均不能为空。"]}

    runner = _tools_root() / "FbxModifierTool" / "run_handoff.py"
    if not runner.is_file():
        return {"success": False, "errors": ["未找到 FBX 后处理入口：{0}".format(runner)]}

    mapping_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as handle:
            json.dump(mapping or {}, handle, ensure_ascii=False)
            mapping_file = handle.name
        command = _find_handoff_command() + ["--source", source_path, "--output", output_path, "--mapping-json", mapping_file]
        if overwrite:
            command.append("--overwrite")
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        stdout = (completed.stdout or "").strip()
        payload = json.loads(stdout) if stdout else {"success": False, "errors": [completed.stderr or "FBX 后处理没有输出结果。"]}
        if completed.returncode and payload.get("success"):
            payload["success"] = False
            payload.setdefault("errors", []).append("FBX 后处理异常退出：{0}".format(completed.returncode))
        return payload
    except Exception as exc:
        return {"success": False, "errors": ["启动 FBX 后处理失败：{0}".format(exc)]}
    finally:
        if mapping_file:
            try:
                os.remove(mapping_file)
            except OSError:
                pass


def inspect_fbx(source_path):
    """Read canonical FBX identities in the bundled FBX SDK environment."""
    source_path = str(source_path or "")
    runner = _tools_root() / "FbxModifierTool" / "run_handoff.py"
    if not source_path or not Path(source_path).is_file():
        return {"success": False, "errors": ["FBX 源文件不存在：{0}".format(source_path)]}
    try:
        completed = subprocess.run(
            _find_handoff_command() + ["--source", source_path, "--inspect"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads((completed.stdout or "").strip())
        if completed.returncode and payload.get("success"):
            payload["success"] = False
        return payload
    except Exception as exc:
        return {"success": False, "errors": ["读取 FBX 身份快照失败：{0}".format(exc)]}
