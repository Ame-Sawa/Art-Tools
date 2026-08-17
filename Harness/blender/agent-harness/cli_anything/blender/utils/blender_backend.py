"""Blender backend — invoke Blender headless for rendering.

Resolution order for the Blender executable:
1. `CLI_ANYTHING_BLENDER_PATH` environment variable
2. `BLENDER_PATH` environment variable
3. `.env.local` found by walking upward from this file
4. `blender` available on PATH
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def _read_local_env_value(key: str) -> Optional[str]:
    """Walk upward and read the first matching key from `.env.local`."""
    for parent in Path(__file__).resolve().parents:
        env_file = parent / ".env.local"
        if not env_file.is_file():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() != key:
                continue
            return value.strip().strip("'\"")
    return None


def _candidate_blender_paths() -> list[str]:
    candidates = []
    for key in ("CLI_ANYTHING_BLENDER_PATH", "BLENDER_PATH"):
        value = os.environ.get(key) or _read_local_env_value(key)
        if value:
            candidates.append(value)
    return candidates


def find_blender() -> str:
    """Find the Blender executable. Raises RuntimeError if not found."""
    for candidate in _candidate_blender_paths():
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    for name in ("blender",):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "Blender executable not found.\n"
        "Set CLI_ANYTHING_BLENDER_PATH or BLENDER_PATH,\n"
        "or put `blender` on PATH."
    )


def get_version() -> str:
    """Get the installed Blender version string."""
    blender = find_blender()
    result = subprocess.run(
        [blender, "--version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    return result.stdout.strip().split("\n")[0]


def render_script(
    script_path: str,
    timeout: int = 300,
) -> dict:
    """Run a bpy script using Blender headless.

    Args:
        script_path: Path to the Python script to execute
        timeout: Maximum seconds to wait

    Returns:
        Dict with stdout, stderr, return code
    """
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script not found: {script_path}")

    blender = find_blender()
    cmd = [blender, "--background", "--python", script_path]

    result = subprocess.run(
        cmd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )

    return {
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_blender_script(
    script_content: str,
    timeout: int = 300,
) -> dict:
    """Run an arbitrary bpy script in Blender headlessly.

    Unlike :func:`render_scene_headless`, this helper does not assume that
    Blender will create a particular output artifact. It is intended for
    import/export and other non-rendering workflows.
    """
    if timeout < 1:
        raise ValueError("Blender timeout must be positive.")

    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, prefix="blender_script_"
    ) as f:
        f.write(script_content)
        script_path = f.name

    try:
        return render_script(script_path, timeout=timeout)
    finally:
        os.unlink(script_path)


def render_scene_headless(
    bpy_script_content: str,
    output_path: str,
    timeout: int = 300,
) -> dict:
    """Write a bpy script to a temp file and render with Blender headless.

    Args:
        bpy_script_content: The bpy Python script as a string
        output_path: Expected output path (set in the script)
        timeout: Maximum seconds to wait

    Returns:
        Dict with output path, file size, method, blender version
    """
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, prefix="blender_render_"
    ) as f:
        f.write(bpy_script_content)
        script_path = f.name

    try:
        result = render_script(script_path, timeout=timeout)

        if result["returncode"] != 0:
            raise RuntimeError(
                f"Blender render failed (exit {result['returncode']}):\n"
                f"  stderr: {result['stderr'][-500:]}"
            )

        # Verify the output file was created
        # Blender appends frame number to output path for single frames
        # e.g., /tmp/render.png becomes /tmp/render0001.png
        actual_output = output_path
        if not os.path.exists(actual_output):
            # Try with frame number suffix
            base, ext = os.path.splitext(output_path)
            for suffix in ["0001", "0000", "1"]:
                candidate = f"{base}{suffix}{ext}"
                if os.path.exists(candidate):
                    actual_output = candidate
                    break

        if not os.path.exists(actual_output):
            raise RuntimeError(
                f"Blender render produced no output file.\n"
                f"  Expected: {output_path}\n"
                f"  stdout: {result['stdout'][-500:]}"
            )

        return {
            "output": os.path.abspath(actual_output),
            "format": os.path.splitext(actual_output)[1].lstrip("."),
            "method": "blender-headless",
            "blender_version": get_version(),
            "file_size": os.path.getsize(actual_output),
        }
    finally:
        os.unlink(script_path)
