"""Blender backend — invoke Blender headless for rendering.

Resolution order for the Blender executable:
1. `CLI_ANYTHING_BLENDER_PATH` environment variable
2. `BLENDER_PATH` environment variable
3. `.env.local` found by walking upward from this file
4. `blender` available on PATH
"""

import os
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional


class CancellationRequested(RuntimeError):
    """Raised when a managed Blender operation is cooperatively cancelled."""


def _terminate_process_tree(process: subprocess.Popen) -> dict:
    """Terminate a process and all descendants, returning cleanup diagnostics."""

    cleanup = {"attempted": True, "ok": False, "method": None, "details": None}
    if os.name == "nt":
        cleanup["method"] = "taskkill"
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            cleanup["ok"] = result.returncode == 0
            cleanup["details"] = (result.stderr or result.stdout or "").strip()[-500:]
        except Exception as error:  # pragma: no cover - platform/tool failure
            cleanup["details"] = str(error)
    else:
        cleanup["method"] = "process_group"
        try:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
            cleanup["ok"] = True
        except Exception as error:  # pragma: no cover - platform/tool failure
            cleanup["details"] = str(error)
    try:
        process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=2)
    except Exception as error:  # pragma: no cover - defensive cleanup path
        cleanup["details"] = "; ".join(
            part for part in (cleanup.get("details"), str(error)) if part
        )
    return cleanup


class ProcessRegistry:
    """Thread-safe registry for active Blender processes and cleanup metadata."""

    def __init__(self, state_file: Optional[str] = None):
        self._lock = threading.RLock()
        self._records: dict[int, dict] = {}
        self._temp_paths: set[str] = set()
        self.state_file = os.path.abspath(state_file) if state_file else None
        self.metadata: dict = {}
        self._write_state()

    def _write_state(self) -> None:
        if not self.state_file:
            return
        payload = {
            "version": 1,
            "pid": os.getpid(),
            "updated_at": time.time(),
            **self.metadata,
            "processes": [
                {key: value for key, value in record.items() if key != "process"}
                for record in self._records.values()
            ],
            "temp_paths": sorted(self._temp_paths),
        }
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            temp_path = self.state_file + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.state_file)
        except OSError:
            # Process cleanup must continue even if diagnostics cannot be written.
            return

    def register(self, process: subprocess.Popen, kind: str, **metadata) -> None:
        with self._lock:
            self._records[int(process.pid)] = {
                "process": process,
                "pid": int(process.pid),
                "kind": kind,
                **metadata,
                "started_at": time.time(),
            }
            self._write_state()

    def unregister(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._records.pop(int(process.pid), None)
            self._write_state()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(record) for record in self._records.values()]

    def set_metadata(self, **values) -> None:
        with self._lock:
            self.metadata.update(values)
            self._write_state()

    def register_temp_path(self, path: str) -> None:
        with self._lock:
            self._temp_paths.add(os.path.abspath(path))
            self._write_state()

    def unregister_temp_path(self, path: str) -> None:
        with self._lock:
            self._temp_paths.discard(os.path.abspath(path))
            self._write_state()

    def terminate_all(self) -> list[dict]:
        results = []
        with self._lock:
            records = list(self._records.values())
        for record in records:
            try:
                process = record.get("process")
                if process is not None:
                    cleanup = _terminate_process_tree(process)
                else:
                    cleanup = {"attempted": True, "ok": False, "method": "pid-only"}
            except Exception as error:  # pragma: no cover - defensive cleanup path
                cleanup = {"attempted": True, "ok": False, "details": str(error)}
            results.append({**{key: value for key, value in record.items() if key != "process"}, "cleanup": cleanup})
        return results


class CancellationContext:
    """Cooperative cancellation state shared by a CLI batch and its workers."""

    def __init__(
        self,
        cancel_file: Optional[str] = None,
        state_file: Optional[str] = None,
        run_dir: Optional[str] = None,
    ):
        self.event = threading.Event()
        self.cancel_file = os.path.abspath(cancel_file) if cancel_file else None
        self.registry = ProcessRegistry(state_file)
        self.run_dir = os.path.abspath(run_dir) if run_dir else None
        self._lock = threading.RLock()
        self.cancel_reason: Optional[str] = None
        self.cleanup_reports: list[dict] = []

    def is_cancelled(self) -> bool:
        if self.event.is_set():
            return True
        if self.cancel_file and os.path.isfile(self.cancel_file):
            self.request_cancel("cancel-file")
        return self.event.is_set()

    def request_cancel(self, reason: str = "requested") -> None:
        with self._lock:
            self.cancel_reason = reason
            self.event.set()
        self.registry.set_metadata(cancelled=True, cancel_reason=reason)

    def register_process(self, process: subprocess.Popen, kind: str, **metadata) -> None:
        self.registry.register(process, kind, **metadata)

    def unregister_process(self, process: subprocess.Popen) -> None:
        self.registry.unregister(process)

    def register_temp_path(self, path: str) -> None:
        self.registry.register_temp_path(path)

    def unregister_temp_path(self, path: str) -> None:
        self.registry.unregister_temp_path(path)

    def create_temp_dir(self, prefix: str = "task-") -> str:
        base = self.run_dir or tempfile.gettempdir()
        os.makedirs(base, exist_ok=True)
        path = tempfile.mkdtemp(prefix=prefix, dir=base)
        self.register_temp_path(path)
        return path

    def terminate_all(self) -> list[dict]:
        reports = self.registry.terminate_all()
        with self._lock:
            self.cleanup_reports.extend(reports)
        return reports

    def close(self, remove_state: bool = True) -> None:
        if not remove_state:
            return
        if self.run_dir:
            shutil.rmtree(self.run_dir, ignore_errors=True)
            return
        if self.registry.state_file:
            try:
                os.unlink(self.registry.state_file)
            except FileNotFoundError:
                pass


AUTOUV_RUN_ROOT = os.path.join(tempfile.gettempdir(), "cli-anything-blender-autouv")


def cleanup_stale_autouv_runs(max_age_seconds: float = 86400.0) -> list[str]:
    """Remove only old Harness-owned cancellation manifests and temp folders."""

    removed = []
    if not os.path.isdir(AUTOUV_RUN_ROOT):
        return removed
    now = time.time()
    for entry in Path(AUTOUV_RUN_ROOT).glob("run-*"):
        if not entry.is_dir():
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        pid = int(manifest.get("pid") or 0)
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except (OSError, ProcessLookupError):
                alive = False
        if alive and age < max_age_seconds:
            continue
        if alive:
            # A live process owns the manifest; never remove its run directory.
            continue
        for path in manifest.get("temp_paths", []):
            try:
                candidate = os.path.abspath(str(path))
                run_root = os.path.abspath(AUTOUV_RUN_ROOT)
                is_harness_temp = os.path.commonpath((candidate, run_root)) == run_root
                is_hidden_staging = (
                    os.path.basename(candidate).startswith(".")
                    and Path(candidate).suffix.lower() in {".fbx", ".obj"}
                )
                is_blender_script = (
                    os.path.basename(candidate).startswith("blender_script_")
                    and Path(candidate).suffix.lower() == ".py"
                )
                if is_harness_temp or is_hidden_staging or is_blender_script:
                    os.unlink(candidate)
            except (OSError, ValueError):
                pass
        for process in manifest.get("processes", []):
            try:
                candidate = os.path.abspath(str(process.get("script_path", "")))
                is_blender_script = (
                    os.path.basename(candidate).startswith("blender_script_")
                    and Path(candidate).suffix.lower() == ".py"
                )
                if candidate and is_blender_script:
                    os.unlink(candidate)
            except (OSError, ValueError):
                pass
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(str(entry))
    return removed


def create_cancellation_context(cancel_file: Optional[str] = None) -> CancellationContext:
    """Create a run-scoped cancellation context and crash-recovery manifest."""

    cleanup_stale_autouv_runs()
    os.makedirs(AUTOUV_RUN_ROOT, exist_ok=True)
    run_dir = tempfile.mkdtemp(prefix="run-", dir=AUTOUV_RUN_ROOT)
    context = CancellationContext(
        cancel_file=cancel_file,
        state_file=os.path.join(run_dir, "manifest.json"),
        run_dir=run_dir,
    )
    context.registry.set_metadata(
        run_dir=run_dir,
        cancel_file=os.path.abspath(cancel_file) if cancel_file else None,
        created_at=time.time(),
    )
    return context


def _run_managed_process(
    command: list[str],
    *,
    timeout: float,
    cwd: Optional[str] = None,
    cancellation: Optional[CancellationContext] = None,
    kind: str = "process",
    **metadata,
) -> dict:
    """Run a child without blocking cancellation or losing its process tree."""

    popen_kwargs = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    if cancellation:
        cancellation.register_process(process, kind, **metadata)

    holder: dict[str, object] = {}

    def communicate() -> None:
        try:
            holder["stdout"], holder["stderr"] = process.communicate()
            holder["returncode"] = process.returncode
        except BaseException as error:  # surfaced on the caller thread below
            holder["error"] = error

    reader = threading.Thread(target=communicate, name=f"blender-io-{process.pid}", daemon=True)
    reader.start()
    deadline = time.monotonic() + max(0.1, float(timeout))
    cleanup = None
    try:
        while reader.is_alive():
            reader.join(0.1)
            if cancellation and cancellation.is_cancelled():
                cleanup = _terminate_process_tree(process)
                reader.join(2)
                raise CancellationRequested(
                    f"{kind} process cancelled; cleanup {'succeeded' if cleanup.get('ok') else 'failed'}"
                )
            if time.monotonic() >= deadline:
                cleanup = _terminate_process_tree(process)
                reader.join(2)
                raise subprocess.TimeoutExpired(command, timeout)
        if "error" in holder:
            raise holder["error"]
        return {
            "returncode": int(holder.get("returncode", process.returncode)),
            "stdout": str(holder.get("stdout", "") or ""),
            "stderr": str(holder.get("stderr", "") or ""),
        }
    finally:
        if cancellation:
            cancellation.unregister_process(process)


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
    *,
    cancellation: Optional[CancellationContext] = None,
    process_kind: str = "blender",
    **metadata,
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

    result = _run_managed_process(
        cmd,
        timeout=timeout,
        cancellation=cancellation,
        kind=process_kind,
        script_path=os.path.abspath(script_path),
        **metadata,
    )

    return {
        "command": " ".join(cmd),
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def run_blender_script(
    script_content: str,
    timeout: int = 300,
    *,
    cancellation: Optional[CancellationContext] = None,
    **metadata,
) -> dict:
    """Run an arbitrary bpy script in Blender headlessly.

    Unlike :func:`render_scene_headless`, this helper does not assume that
    Blender will create a particular output artifact. It is intended for
    import/export and other non-rendering workflows.
    """
    if timeout < 1:
        raise ValueError("Blender timeout must be positive.")

    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", encoding="utf-8", delete=False, prefix="blender_script_"
    ) as f:
        f.write(script_content)
        script_path = f.name

    try:
        render_kwargs = {}
        if cancellation is not None:
            render_kwargs["cancellation"] = cancellation
        if metadata:
            render_kwargs.update(metadata)
        if cancellation is not None:
            cancellation.register_temp_path(script_path)
        return render_script(script_path, timeout=timeout, **render_kwargs)
    finally:
        try:
            os.unlink(script_path)
        except FileNotFoundError:
            pass
        if cancellation is not None:
            cancellation.unregister_temp_path(script_path)


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
        suffix=".py", mode="w", encoding="utf-8", delete=False, prefix="blender_render_"
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
