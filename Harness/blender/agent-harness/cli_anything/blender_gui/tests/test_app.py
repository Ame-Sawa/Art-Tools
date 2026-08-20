from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication

import cli_anything.blender_gui.app as app_module


@pytest.fixture(scope="module")
def qapp():
    application = QApplication.instance() or QApplication([])
    return application


def make_fbx(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"placeholder")
    return path


def test_main_window_is_batch_only_and_starts_empty(qapp, monkeypatch):
    monkeypatch.setattr(app_module, "load_settings", lambda: {})
    window = app_module.UniformUVWindow()

    assert not hasattr(window, "source_edit")
    assert not hasattr(window, "output_edit")
    assert window.batch_paths == []
    assert window.batch_count_label.text() == "已选择 0 个 FBX 文件"
    assert not window.run_button.isEnabled()
    assert window.output_source_radio.isChecked()
    assert not window.output_dir_edit.isEnabled()
    assert window.topology_prefilter_combo.currentData() == "high"
    assert window.parallel_jobs_spin.value() == 2
    assert window.parallel_jobs_spin.maximum() == 50
    assert window.timeout_spin.value() == 300
    assert window.external_timeout_spin.value() == 120
    assert window.merge_meshes_check.isChecked()
    assert window.normalize_uv_check.isChecked()
    assert "跨 Mesh 合并展开并归一化 UV" in window.autouv_status_label.text()
    assert window.activity_table.columnCount() == 5
    assert "#000000" in window.activity_table.styleSheet()


def test_batch_dialog_shows_output_preview_and_deletes_selected(qapp, tmp_path):
    source = make_fbx(tmp_path, "asset.fbx")
    output_dir = tmp_path / "output"
    dialog = app_module.BatchInputDialog(
        [str(source)], "autouv", "path", str(output_dir), "", None,
    )

    assert dialog.table_widget.rowCount() == 1
    assert dialog.table_widget.columnCount() == 3
    assert dialog.table_widget.item(0, 0).text() == "asset.fbx"
    assert dialog.table_widget.item(0, 1).text() == str(source.resolve())
    assert dialog.table_widget.item(0, 2).text() == str(output_dir / "asset.fbx")
    assert dialog.table_widget.item(0, 1).toolTip() == str(source.resolve())
    assert dialog.table_widget.item(0, 2).toolTip() == str(output_dir / "asset.fbx")
    assert "#000000" in dialog.table_widget.styleSheet()

    dialog.table_widget.selectRow(0)
    dialog._remove_selected()
    assert dialog.paths == ()


def test_batch_dialog_deletes_multiple_selected_rows(qapp, tmp_path):
    sources = [make_fbx(tmp_path, f"asset_{index}.fbx") for index in range(3)]
    dialog = app_module.BatchInputDialog(
        [str(path) for path in sources], "autouv", "suffix", "", "_autouv", None,
    )

    selection = dialog.table_widget.selectionModel()
    for row in (0, 2):
        selection.select(
            dialog.table_widget.model().index(row, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
    dialog._remove_selected()

    assert dialog.paths == (str(sources[1].resolve()),)
    assert dialog.table_widget.rowCount() == 1


def test_batch_dialog_deduplicates_imported_paths(qapp, monkeypatch, tmp_path):
    source_a = make_fbx(tmp_path, "a.fbx")
    source_b = make_fbx(tmp_path, "b.fbx")
    dialog = app_module.BatchInputDialog([], "autouv", "suffix", "", "_autouv", None)
    monkeypatch.setattr(
        app_module.QFileDialog,
        "getOpenFileNames",
        lambda *args, **kwargs: ([str(source_a), str(source_a), str(source_b)], ""),
    )

    dialog._add_files()

    assert dialog.paths == (str(source_a.resolve()), str(source_b.resolve()))


def test_batch_dialog_cancel_does_not_mutate_existing_paths(qapp, tmp_path):
    source = make_fbx(tmp_path, "asset.fbx")
    original = [str(source)]
    dialog = app_module.BatchInputDialog(original, "autouv", "suffix", "", "_autouv", None)
    dialog._paths.clear()
    dialog.reject()

    assert original == [str(source)]


@pytest.mark.parametrize(
    ("saved", "expected"),
    [({"topology_prefilter": True}, "high"), ({"topology_prefilter": False}, "off"), ({}, "high")],
)
def test_gui_migrates_topology_prefilter_settings(qapp, monkeypatch, saved, expected):
    monkeypatch.setattr(app_module, "load_settings", lambda: saved)
    window = app_module.UniformUVWindow()

    assert window.topology_prefilter_combo.currentData() == expected


def test_gui_migrates_legacy_global_pack_and_updates_autouv_status(qapp, monkeypatch):
    monkeypatch.setattr(
        app_module, "load_settings",
        lambda: {"global_pack": False, "normalize_uv": True},
    )
    window = app_module.UniformUVWindow()
    assert not window.merge_meshes_check.isChecked()
    assert "逐 Mesh 展开并归一化 UV" in window.autouv_status_label.text()

    window.udims_spin.setValue(2)
    assert "UDIM>1" in window.autouv_status_label.text()
    window.udims_spin.setValue(1)
    window.world_scale_check.setChecked(True)
    assert "绝对纹素密度可能改变" in window.autouv_status_label.text()


def test_gui_progress_events_update_file_count_and_status(qapp):
    window = app_module.UniformUVWindow()

    window._handle_progress_event({"event": "batch_started", "total": 2})
    assert window.progress.maximum() == 2
    assert window.progress.value() == 0

    window._handle_progress_event({
        "event": "file_started",
        "index": 1,
        "total": 2,
        "input_fbx": r"H:\models\a.fbx",
    })
    assert "1/2" in window.status_label.text()
    assert "a.fbx" in window.status_label.text()

    window._handle_progress_event({
        "event": "file_finished",
        "index": 1,
        "total": 2,
        "input_fbx": r"H:\models\a.fbx",
        "ok": True,
    })
    assert window.progress.value() == 1

    window._handle_progress_event({
        "event": "file_finished",
        "index": 2,
        "total": 2,
        "input_fbx": r"H:\models\b.fbx",
        "ok": False,
        "error": "simulated failure",
    })
    assert window.progress.value() == 2
    assert window.activity_table.rowCount() == 2
    assert window.activity_table.item(1, 1).text() == "失败"
    assert "simulated failure" in window.activity_table.item(1, 4).text()


def test_gui_progress_events_display_topology_skip(qapp):
    window = app_module.UniformUVWindow()
    window._handle_progress_event({"event": "batch_started", "total": 1})
    window._handle_progress_event({
        "event": "file_finished",
        "index": 1,
        "total": 1,
        "input_fbx": r"H:\models\high-risk.fbx",
        "ok": False,
        "skipped": True,
        "risk_score": 15,
        "preflight": {"risk_score": 15, "risk_level": "high"},
    })

    assert window.activity_table.item(0, 1).text() == "跳过"
    assert "15" in window.activity_table.item(0, 4).text()
    assert window.progress.value() == 1

    formatted = window._format_result({
        "algorithm": "autouv",
        "total": 1,
        "success_count": 0,
        "failure_count": 0,
        "skipped_count": 1,
        "results": [{
            "input_fbx": r"H:\models\high-risk.fbx",
            "ok": False,
            "skipped": True,
            "preflight": {"risk_score": 15, "risk_level": "high"},
        }],
    })
    assert "跳过：1" in formatted
    assert "[跳过]" in formatted


def test_gui_progress_events_display_cancelled_file(qapp):
    window = app_module.UniformUVWindow()
    window._handle_progress_event({"event": "batch_started", "total": 1})
    window._handle_progress_event({
        "event": "file_finished",
        "index": 1,
        "total": 1,
        "completed_count": 1,
        "input_fbx": r"H:\models\cancelled.fbx",
        "ok": False,
        "cancelled": True,
        "skip_reason": "cancelled",
        "error": "Batch cancelled before this file started.",
    })

    assert window.activity_table.item(0, 1).text() == "已取消"
    assert "cancelled" in window.activity_table.item(0, 4).text()


def test_gui_parallel_progress_uses_completed_count_not_input_index(qapp):
    window = app_module.UniformUVWindow()
    window._handle_progress_event({
        "event": "batch_started", "total": 3, "jobs": 2, "effective_jobs": 2,
    })
    window._handle_progress_event({
        "event": "file_finished", "index": 3, "total": 3,
        "completed_count": 1, "active_jobs": 1,
        "input_fbx": r"H:\models\c.fbx", "ok": True,
        "duration_seconds": 1.25,
    })
    assert window.progress.value() == 1
    assert window.activity_table.item(0, 1).text() == "成功"
    assert "1.25" in window.activity_table.item(0, 4).text()

    window._handle_progress_event({
        "event": "file_finished", "index": 1, "total": 3,
        "completed_count": 2, "active_jobs": 1,
        "input_fbx": r"H:\models\a.fbx", "ok": False,
        "error": "failed", "duration_seconds": 2.5,
    })
    assert window.progress.value() == 2
    assert window.activity_table.item(1, 1).text() == "失败"
    assert "2.50" in window.activity_table.item(1, 4).text()


def test_gui_error_message_includes_exit_code_and_diagnostics(qapp):
    message = app_module.UniformUVWindow._error_message("1", "Blender failed", 1)

    assert message != "1"
    assert "退出码 1" in message
    assert "Blender failed" in message


def test_gui_stderr_line_parser_separates_progress_from_diagnostics(qapp):
    window = app_module.UniformUVWindow()

    window._consume_stderr_line(json.dumps({"event": "batch_started", "total": 3}))
    window._consume_stderr_line("ordinary diagnostic")

    assert window.progress.maximum() == 3
    assert "ordinary diagnostic" in window._stderr
    assert window.activity_table.item(0, 1).text() == "日志"
    assert "ordinary diagnostic" in window.activity_table.item(0, 4).text()
