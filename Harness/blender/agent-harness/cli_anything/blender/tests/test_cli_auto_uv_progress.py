from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from cli_anything.blender import blender_cli


def make_fbx(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"placeholder")
    return path


def test_json_auto_uv_progress_stays_on_stderr(monkeypatch, tmp_path):
    source_a = make_fbx(tmp_path, "a.fbx")
    source_b = make_fbx(tmp_path, "b.fbx")

    def fake_batch(paths, *, progress_callback=None, **kwargs):
        total = len(paths)
        if progress_callback:
            progress_callback({"event": "batch_started", "algorithm": "autouv", "total": total})
            for index, path in enumerate(paths, start=1):
                progress_callback({
                    "event": "file_started",
                    "algorithm": "autouv",
                    "index": index,
                    "total": total,
                    "input_fbx": str(path),
                })
                progress_callback({
                    "event": "file_finished",
                    "algorithm": "autouv",
                    "index": index,
                    "total": total,
                    "input_fbx": str(path),
                    "ok": True,
                    "output_fbx": str(path),
                })
            progress_callback({
                "event": "batch_finished",
                "algorithm": "autouv",
                "total": total,
                "success_count": total,
                "failure_count": 0,
            })
        return {
            "algorithm": "autouv",
            "total": total,
            "success_count": total,
            "failure_count": 0,
            "results": [
                {"input_fbx": str(path), "output_fbx": str(path), "ok": True, "result": {}}
                for path in paths
            ],
        }

    monkeypatch.setattr(blender_cli.fbx_mod, "export_fbx_auto_uv_batch", fake_batch)
    result = CliRunner().invoke(
        blender_cli.cli,
        ["--json", "fbx", "auto-uv", str(source_a), str(source_b)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["success_count"] == 2
    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert [event["event"] for event in events] == [
        "batch_started",
        "file_started",
        "file_finished",
        "file_started",
        "file_finished",
        "batch_finished",
    ]


def test_json_auto_uv_failure_keeps_summary_clean(monkeypatch, tmp_path):
    source = make_fbx(tmp_path, "failed.fbx")

    def fake_batch(paths, *, progress_callback=None, **kwargs):
        if progress_callback:
            progress_callback({
                "event": "batch_started", "algorithm": "autouv", "total": 1,
            })
            progress_callback({
                "event": "file_started", "algorithm": "autouv", "index": 1,
                "total": 1, "input_fbx": str(paths[0]),
            })
            progress_callback({
                "event": "file_finished", "algorithm": "autouv", "index": 1,
                "total": 1, "input_fbx": str(paths[0]), "ok": False,
                "error": "external tool crashed (0xC0000005)",
            })
            progress_callback({
                "event": "batch_finished", "algorithm": "autouv", "total": 1,
                "success_count": 0, "failure_count": 1,
            })
        return {
            "algorithm": "autouv", "total": 1, "success_count": 0,
            "failure_count": 1,
            "results": [{
                "input_fbx": str(paths[0]), "ok": False,
                "error": "external tool crashed (0xC0000005)",
            }],
        }

    monkeypatch.setattr(blender_cli.fbx_mod, "export_fbx_auto_uv_batch", fake_batch)
    result = CliRunner().invoke(
        blender_cli.cli,
        ["--json", "fbx", "auto-uv", str(source)],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["failure_count"] == 1
    assert "type\": \"Exit\"" not in result.stdout
    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert events[-1]["event"] == "batch_finished"


def test_json_auto_uv_skip_is_reported_on_stderr_and_counts_as_nonzero(monkeypatch, tmp_path):
    source = make_fbx(tmp_path, "high-risk.fbx")

    def fake_batch(paths, *, progress_callback=None, **kwargs):
        if progress_callback:
            progress_callback({
                "event": "batch_started", "algorithm": "autouv", "total": 1,
            })
            progress_callback({
                "event": "file_started", "algorithm": "autouv", "index": 1,
                "total": 1, "input_fbx": str(paths[0]),
            })
            progress_callback({
                "event": "file_finished", "algorithm": "autouv", "index": 1,
                "total": 1, "input_fbx": str(paths[0]), "ok": False,
                "skipped": True, "risk_score": 15,
                "preflight": {"risk_score": 15, "risk_level": "high"},
            })
            progress_callback({
                "event": "batch_finished", "algorithm": "autouv", "total": 1,
                "success_count": 0, "failure_count": 0, "skipped_count": 1,
            })
        return {
            "algorithm": "autouv", "total": 1, "success_count": 0,
            "failure_count": 0, "skipped_count": 1,
            "results": [{
                "input_fbx": str(paths[0]), "ok": False, "skipped": True,
                "skip_reason": "topology_risk",
                "preflight": {"risk_score": 15, "risk_level": "high"},
            }],
        }

    monkeypatch.setattr(blender_cli.fbx_mod, "export_fbx_auto_uv_batch", fake_batch)
    result = CliRunner().invoke(
        blender_cli.cli,
        ["--json", "fbx", "auto-uv", str(source)],
    )

    assert result.exit_code == 1
    summary = json.loads(result.stdout)
    assert summary["skipped_count"] == 1
    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert events[2]["skipped"] is True
    assert events[2]["risk_score"] == 15
    assert events[-1]["skipped_count"] == 1


def test_cli_passes_topology_prefilter_level(monkeypatch, tmp_path):
    source = make_fbx(tmp_path, "strict.fbx")
    observed = {}

    def fake_batch(paths, *, progress_callback=None, **kwargs):
        observed.update(kwargs)
        return {
            "algorithm": "autouv", "total": 1, "success_count": 1,
            "failure_count": 0, "skipped_count": 0,
            "results": [{"input_fbx": str(paths[0]), "output_fbx": str(paths[0]), "ok": True}],
        }

    monkeypatch.setattr(blender_cli.fbx_mod, "export_fbx_auto_uv_batch", fake_batch)
    result = CliRunner().invoke(
        blender_cli.cli,
        ["--json", "fbx", "auto-uv", str(source), "--topology-prefilter-level", "medium"],
    )

    assert result.exit_code == 0, result.output
    assert observed["topology_prefilter_level"] == "medium"
    assert observed["topology_prefilter"] is None


def test_cli_passes_parallel_jobs(monkeypatch, tmp_path):
    source_a = make_fbx(tmp_path, "a.fbx")
    source_b = make_fbx(tmp_path, "b.fbx")
    observed = {}

    def fake_batch(paths, *, progress_callback=None, **kwargs):
        observed.update(kwargs)
        return {
            "algorithm": "autouv", "total": 2, "jobs": 4, "effective_jobs": 2,
            "success_count": 2, "failure_count": 0, "skipped_count": 0,
            "results": [
                {"input_fbx": str(path), "output_fbx": str(path), "ok": True}
                for path in paths
            ],
        }

    monkeypatch.setattr(blender_cli.fbx_mod, "export_fbx_auto_uv_batch", fake_batch)
    result = CliRunner().invoke(
        blender_cli.cli,
        ["--json", "fbx", "auto-uv", str(source_a), str(source_b), "--jobs", "4"],
    )

    assert result.exit_code == 0, result.output
    assert observed["jobs"] == 4


def test_cli_rejects_legacy_and_level_prefilter_options_together(tmp_path):
    source = make_fbx(tmp_path, "conflict.fbx")
    result = CliRunner().invoke(
        blender_cli.cli,
        [
            "--json", "fbx", "auto-uv", str(source),
            "--topology-prefilter", "--topology-prefilter-level", "high",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "topology_prefilter_level" in payload["error"]
