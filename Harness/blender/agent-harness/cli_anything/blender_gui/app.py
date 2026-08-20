"""PySide6 desktop front-end for safe multi-file AutoUV processing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cli_anything.blender.utils.blender_backend import find_blender
from .command import (
    AUTO_UV_DEFAULTS,
    DEFAULT_ANGLE_DEGREES,
    DEFAULT_PARALLEL_JOBS,
    MAX_PARALLEL_JOBS,
    build_batch_uv_args,
    batch_output_paths,
    parse_cli_json,
    resolve_cli_invocation,
    validate_batch_request,
)
from .settings import load_settings, save_settings


TABLE_STYLE = (
    "QTableWidget { border: 1px solid #000000; gridline-color: #000000; }"
    "QTableWidget::item { border: 1px solid #000000; }"
    "QHeaderView::section { border: 1px solid #000000; }"
)
STATUS_COLORS = {
    "成功": QColor("#C6EFCE"),
    "失败": QColor("#FFC7CE"),
    "跳过": QColor("#FCE4D6"),
    "处理中": QColor("#D9EAF7"),
    "待处理": QColor("#FFF2CC"),
    "已取消": QColor("#D9D9D9"),
    "日志": QColor("#E7E6E6"),
}


class BatchInputDialog(QDialog):
    """Multi-select FBX input editor used by the batch workflow."""

    def __init__(self, paths, algorithm, output_mode, output_dir, suffix, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入 FBX")
        self.resize(1100, 520)
        self._paths = []
        known = set()
        for path in paths:
            normalized = os.path.abspath(path)
            key = os.path.normcase(normalized)
            if key not in known:
                known.add(key)
                self._paths.append(normalized)
        self._algorithm = algorithm
        self._output_mode = output_mode
        self._output_dir = output_dir
        self._suffix = suffix

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择即将处理的 FBX 文件；每行一个文件，可多选后删除。"))
        self.table_widget = QTableWidget(0, 3)
        self.table_widget.setHorizontalHeaderLabels(("文件名", "输入路径", "预计输出路径"))
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSortingEnabled(False)
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setStyleSheet(TABLE_STYLE)
        header = self.table_widget.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table_widget, 1)

        action_row = QHBoxLayout()
        add_button = QPushButton("从文件夹导入")
        add_button.clicked.connect(self._add_files)
        remove_button = QPushButton("删除选中")
        remove_button.clicked.connect(self._remove_selected)
        action_row.addWidget(add_button)
        action_row.addWidget(remove_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    @property
    def paths(self):
        return tuple(self._paths)

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "从文件夹导入 FBX",
            "",
            "FBX 文件 (*.fbx)",
        )
        known = {os.path.normcase(os.path.abspath(path)) for path in self._paths}
        for path in paths:
            normalized = os.path.abspath(path)
            key = os.path.normcase(normalized)
            if key not in known:
                known.add(key)
                self._paths.append(normalized)
        self._refresh()

    def _remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.table_widget.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self._paths):
                del self._paths[row]
        self._refresh()

    def _planned_output(self, path: str) -> str:
        source = Path(path)
        if self._output_mode == "source":
            return "覆盖源文件"
        suffix = self._suffix if self._output_mode == "suffix" else ""
        if self._output_mode == "suffix" and not suffix:
            suffix = "_autouv" if self._algorithm == "autouv" else "_uv"
        if self._output_mode == "path" and self._output_dir:
            return str(Path(self._output_dir) / f"{source.stem}{suffix}{source.suffix}")
        return str(source.with_name(f"{source.stem}{suffix}{source.suffix}"))

    def _refresh(self) -> None:
        self.table_widget.setRowCount(0)
        for path in self._paths:
            row = self.table_widget.rowCount()
            self.table_widget.insertRow(row)
            values = (Path(path).name, path, self._planned_output(path))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table_widget.setItem(row, column, item)

    def _accept(self) -> None:
        if not self._paths:
            QMessageBox.warning(self, "没有输入文件", "请至少导入一个 FBX 文件。")
            return
        self.accept()


class UniformUVWindow(QMainWindow):
    """Batch-only main window for the Uniform UV and AutoUV workflows."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Blender UV Tools")
        self.resize(820, 720)

        self.process: Optional[QProcess] = None
        self._stdout = ""
        self._stderr = ""
        self._stderr_buffer = ""
        self._cancel_requested = False
        self._force_kill_requested = False
        self._close_after_process = False
        self._cancel_file: Optional[str] = None
        self._cancel_dir: Optional[str] = None
        self._progress_total = 0
        self._progress_completed = 0
        self._output_mode = "source"
        self._output_suffix = "_autouv"
        self._algorithm = "autouv"
        self.batch_paths: list[str] = []

        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItem("Blender Uniform UV", "uniform")
        self.algorithm_combo.addItem("Ministry of Flat AutoUV", "autouv")
        self.algorithm_combo.currentIndexChanged.connect(self._algorithm_changed)
        self.algorithm_combo.blockSignals(True)
        self.algorithm_combo.setCurrentIndex(1)
        self.algorithm_combo.blockSignals(False)

        self.blender_edit = QLineEdit()
        self.blender_edit.setPlaceholderText("留空则使用 CLI 的自动发现逻辑")
        self.unwrap_exe_edit = QLineEdit()
        self.unwrap_exe_edit.setPlaceholderText("留空则自动搜索 UnWrapConsole3.exe")

        self.output_source_radio = QRadioButton("覆盖源文件")
        self.output_suffix_radio = QRadioButton("添加固定后缀")
        self.output_dir_radio = QRadioButton("输出到指定目录")
        self.output_source_radio.setChecked(True)
        self.output_source_radio.toggled.connect(self._output_mode_changed)
        self.output_suffix_radio.toggled.connect(self._output_mode_changed)
        self.output_dir_radio.toggled.connect(self._output_mode_changed)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setReadOnly(True)
        self.output_dir_edit.setPlaceholderText("选择批量输出目录；输出文件保留原始文件名")
        self.output_dir_browse_button = QPushButton("选择目录…")
        self.output_dir_browse_button.clicked.connect(self._browse_output_dir)

        self.batch_count_label = QLabel("已选择 0 个 FBX 文件")
        self.batch_select_button = QPushButton("选择批处理文件…")
        self.batch_select_button.clicked.connect(self._open_batch_import)

        self.rotate_combo = QComboBox()
        self.rotate_combo.addItem("保持 CLI 默认（垂直对齐）", None)
        self.rotate_combo.addItem("自动选择方向", "AXIS_ALIGNED")
        self.rotate_combo.addItem("水平对齐", "AXIS_ALIGNED_X")
        self.rotate_combo.addItem("垂直对齐", "AXIS_ALIGNED_Y")

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 86400)
        self.timeout_spin.setValue(300)
        self.timeout_spin.setSuffix(" 秒")

        self.external_timeout_spin = QSpinBox()
        self.external_timeout_spin.setRange(1, 86400)
        self.external_timeout_spin.setValue(120)
        self.external_timeout_spin.setSuffix(" 秒")

        self.parallel_jobs_spin = QSpinBox()
        self.parallel_jobs_spin.setRange(1, MAX_PARALLEL_JOBS)
        self.parallel_jobs_spin.setValue(DEFAULT_PARALLEL_JOBS)
        self.parallel_jobs_spin.setSuffix(" 个")
        self.parallel_jobs_spin.setToolTip(
            f"每个 FBX 使用独立 Blender/AutoUV 进程；设置为 1 即串行，最多 {MAX_PARALLEL_JOBS} 个。"
        )

        self.resolution_spin = QSpinBox()
        self.resolution_spin.setRange(1, 16384)
        self.resolution_spin.setValue(AUTO_UV_DEFAULTS["resolution"])
        self.aspect_spin = QDoubleSpinBox()
        self.aspect_spin.setRange(0.000001, 1000.0)
        self.aspect_spin.setDecimals(6)
        self.aspect_spin.setValue(AUTO_UV_DEFAULTS["aspect"])
        self.udims_spin = QSpinBox()
        self.udims_spin.setRange(1, 1000)
        self.udims_spin.setValue(AUTO_UV_DEFAULTS["udims"])
        self.density_spin = QSpinBox()
        self.density_spin.setRange(1, 1000000)
        self.density_spin.setValue(AUTO_UV_DEFAULTS["density"])
        self.separate_edges_check = QCheckBox("分离硬边")
        self.normals_check = QCheckBox("使用法线")
        self.overlap_identical_check = QCheckBox("重叠相同部件")
        self.overlap_mirrored_check = QCheckBox("重叠镜像部件")
        self.world_scale_check = QCheckBox("按世界尺度展开")
        self.merge_meshes_check = QCheckBox("跨 Mesh 合并调用")
        self.merge_meshes_check.setChecked(True)
        self.normalize_uv_check = QCheckBox("UV 归一化")
        self.normalize_uv_check.setChecked(True)
        self.autouv_status_label = QLabel()
        self.autouv_status_label.setWordWrap(True)
        self.autouv_status_label.setObjectName("autouv_status_label")
        self.udims_spin.valueChanged.connect(lambda _value: self._refresh_autouv_status())
        self.world_scale_check.toggled.connect(lambda _checked: self._refresh_autouv_status())
        self.merge_meshes_check.toggled.connect(lambda _checked: self._refresh_autouv_status())
        self.normalize_uv_check.toggled.connect(lambda _checked: self._refresh_autouv_status())
        self.topology_prefilter_combo = QComboBox()
        self.topology_prefilter_combo.setObjectName("topology_prefilter_level")
        self.topology_prefilter_combo.addItem("关闭", "off")
        self.topology_prefilter_combo.addItem("标准：仅跳过高风险", "high")
        self.topology_prefilter_combo.addItem("严格：跳过中/高风险", "medium")
        self.topology_prefilter_combo.setCurrentIndex(1)

        self.angle_checks: dict[float, QCheckBox] = {}
        for angle in DEFAULT_ANGLE_DEGREES:
            check = QCheckBox(f"{int(angle)}°")
            check.setChecked(True)
            self.angle_checks[angle] = check

        self.run_button = QPushButton("开始 AutoUV")
        self.run_button.clicked.connect(self._run)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status_label = QLabel("请选择待处理的 FBX 批次。")
        self.status_label.setWordWrap(True)

        self.activity_table = QTableWidget(0, 5)
        self.activity_table.setObjectName("activity_table")
        self.activity_table.setHorizontalHeaderLabels(("序号", "状态", "文件名", "输出路径", "详情"))
        self.activity_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.activity_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.activity_table.setAlternatingRowColors(True)
        self.activity_table.setSortingEnabled(False)
        self.activity_table.verticalHeader().setVisible(False)
        activity_header = self.activity_table.horizontalHeader()
        activity_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        activity_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        activity_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        activity_header.setSectionResizeMode(3, QHeaderView.Stretch)
        activity_header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.activity_table.setStyleSheet(TABLE_STYLE)
        self._activity_rows: dict[str, int] = {}
        self._activity_log_count = 0

        self._build_ui()
        self._load_saved_settings()
        self._algorithm_changed(self.algorithm_combo.currentIndex())

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setSpacing(10)

        tool_group = QGroupBox("算法与工具")
        tool_layout = QGridLayout(tool_group)
        tool_layout.setColumnStretch(1, 1)
        tool_layout.addWidget(QLabel("算法"), 0, 0)
        tool_layout.addWidget(self.algorithm_combo, 0, 1)
        tool_layout.addWidget(QLabel("Blender"), 1, 0)
        tool_layout.addWidget(self.blender_edit, 1, 1)
        browse_blender = QPushButton("选择 exe…")
        browse_blender.clicked.connect(self._browse_blender)
        tool_layout.addWidget(browse_blender, 1, 2)
        tool_layout.addWidget(QLabel("AutoUV 程序"), 2, 0)
        tool_layout.addWidget(self.unwrap_exe_edit, 2, 1)
        browse_unwrap = QPushButton("选择 exe…")
        browse_unwrap.clicked.connect(self._browse_unwrap_exe)
        tool_layout.addWidget(browse_unwrap, 2, 2)
        root.addWidget(tool_group)

        options_group = QGroupBox("Uniform UV 参数")
        self.uniform_options_group = options_group
        options_layout = QVBoxLayout(options_group)
        angle_row = QHBoxLayout()
        angle_row.addWidget(QLabel("角度候选："))
        for check in self.angle_checks.values():
            angle_row.addWidget(check)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_angles(True))
        clear_all = QPushButton("清空")
        clear_all.clicked.connect(lambda: self._set_all_angles(False))
        angle_row.addWidget(select_all)
        angle_row.addWidget(clear_all)
        angle_row.addStretch(1)
        options_layout.addLayout(angle_row)

        form = QFormLayout()
        form.addRow("岛屿旋转：", self.rotate_combo)
        options_layout.addLayout(form)
        root.addWidget(options_group)

        self.autouv_options_group = QGroupBox("Ministry of Flat AutoUV 参数")
        autouv_layout = QVBoxLayout(self.autouv_options_group)
        autouv_form = QFormLayout()
        autouv_form.addRow("纹理分辨率：", self.resolution_spin)
        autouv_form.addRow("像素宽高比：", self.aspect_spin)
        autouv_form.addRow("UDIM 数量：", self.udims_spin)
        autouv_form.addRow("世界尺度密度：", self.density_spin)
        autouv_form.addRow("外部程序超时：", self.external_timeout_spin)
        autouv_form.addRow("安全拓扑筛选：", self.topology_prefilter_combo)
        autouv_layout.addLayout(autouv_form)
        checks_row = QHBoxLayout()
        for check in (
            self.separate_edges_check,
            self.normals_check,
            self.overlap_identical_check,
            self.overlap_mirrored_check,
            self.world_scale_check,
            self.merge_meshes_check,
            self.normalize_uv_check,
        ):
            checks_row.addWidget(check)
        checks_row.addStretch(1)
        autouv_layout.addLayout(checks_row)
        autouv_layout.addWidget(self.autouv_status_label)
        root.addWidget(self.autouv_options_group)

        runtime_group = QGroupBox("运行设置")
        runtime_layout = QFormLayout(runtime_group)
        runtime_layout.addRow("Blender 总超时：", self.timeout_spin)
        runtime_layout.addRow("并行任务数：", self.parallel_jobs_spin)
        root.addWidget(runtime_group)

        output_group = QGroupBox("输出方式")
        output_layout = QGridLayout(output_group)
        output_layout.setColumnStretch(2, 1)
        output_layout.addWidget(self.output_source_radio, 0, 0, 1, 3)
        output_layout.addWidget(self.output_suffix_radio, 1, 0, 1, 3)
        output_layout.addWidget(self.output_dir_radio, 2, 0)
        output_layout.addWidget(self.output_dir_edit, 2, 1)
        output_layout.addWidget(self.output_dir_browse_button, 2, 2)
        root.addWidget(output_group)

        batch_group = QGroupBox("待处理文件")
        batch_layout = QHBoxLayout(batch_group)
        batch_layout.addWidget(self.batch_count_label)
        batch_layout.addStretch(1)
        batch_layout.addWidget(self.batch_select_button)
        root.addWidget(batch_group)

        action_row = QHBoxLayout()
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.progress, 1)
        action_row.addWidget(self.status_label, 2)
        root.addLayout(action_row)

        activity_group = QGroupBox("日志与结果")
        activity_layout = QVBoxLayout(activity_group)
        activity_layout.addWidget(self.activity_table)
        root.addWidget(activity_group, 2)

        self.setCentralWidget(central)
        self.setStyleSheet(
            "QGroupBox { font-weight: 600; }"
            "QLineEdit { padding: 5px; }"
            "QPushButton { padding: 5px 10px; }"
            + TABLE_STYLE
        )

    def _load_saved_settings(self) -> None:
        values = load_settings()
        saved_blender = values.get("blender_path")
        if isinstance(saved_blender, str) and saved_blender and os.path.isfile(saved_blender):
            self.blender_edit.setText(saved_blender)
        else:
            try:
                self.blender_edit.setText(find_blender())
            except (RuntimeError, OSError):
                pass

        saved_algorithm = values.get("algorithm")
        if saved_algorithm in {"uniform", "autouv"}:
            self.algorithm_combo.setCurrentIndex(0 if saved_algorithm == "uniform" else 1)
        saved_unwrap = values.get("unwrap_exe")
        # Keep old settings compatible, but do not forward a stale path from a
        # previous checkout. An empty field lets the CLI resolve the Harness-
        # bundled executable automatically.
        if isinstance(saved_unwrap, str) and os.path.isfile(saved_unwrap):
            self.unwrap_exe_edit.setText(saved_unwrap)
        for key, widget in (
            ("resolution", self.resolution_spin),
            ("udims", self.udims_spin),
            ("density", self.density_spin),
        ):
            value = values.get(key)
            if isinstance(value, int) and value > 0:
                widget.setValue(value)
        aspect = values.get("aspect")
        if isinstance(aspect, (int, float)) and float(aspect) > 0:
            self.aspect_spin.setValue(float(aspect))
        for key, widget in (
            ("separate_hard_edges", self.separate_edges_check),
            ("use_normals", self.normals_check),
            ("overlap_identical", self.overlap_identical_check),
            ("overlap_mirrored", self.overlap_mirrored_check),
            ("world_scale", self.world_scale_check),
        ):
            value = values.get(key)
            if isinstance(value, bool):
                widget.setChecked(value)
        merge_meshes = values.get("merge_meshes")
        if not isinstance(merge_meshes, bool):
            merge_meshes = values.get("global_pack", True)
        if isinstance(merge_meshes, bool):
            self.merge_meshes_check.setChecked(merge_meshes)
        normalize_uv = values.get("normalize_uv", True)
        if isinstance(normalize_uv, bool):
            self.normalize_uv_check.setChecked(normalize_uv)
        external_timeout = values.get("external_timeout")
        if isinstance(external_timeout, int) and external_timeout > 0:
            self.external_timeout_spin.setValue(min(external_timeout, 86400))
        timeout = values.get("timeout")
        if isinstance(timeout, int) and timeout > 0:
            self.timeout_spin.setValue(min(timeout, 86400))
        parallel_jobs = values.get("parallel_jobs")
        if isinstance(parallel_jobs, int) and parallel_jobs > 0:
            self.parallel_jobs_spin.setValue(min(parallel_jobs, MAX_PARALLEL_JOBS))
        saved_level = values.get("topology_prefilter_level")
        if saved_level not in {"off", "high", "medium"}:
            legacy = values.get("topology_prefilter")
            saved_level = "high" if legacy is not False else "off"
        level_index = self.topology_prefilter_combo.findData(saved_level)
        self.topology_prefilter_combo.setCurrentIndex(level_index if level_index >= 0 else 1)

    def _save_settings(self) -> None:
        blender_path = self.blender_edit.text().strip()
        values = {
            "algorithm": self._algorithm,
            "unwrap_exe": self.unwrap_exe_edit.text().strip(),
            "resolution": self.resolution_spin.value(),
            "aspect": self.aspect_spin.value(),
            "udims": self.udims_spin.value(),
            "density": self.density_spin.value(),
            "external_timeout": self.external_timeout_spin.value(),
            "timeout": self.timeout_spin.value(),
            "parallel_jobs": self.parallel_jobs_spin.value(),
            "separate_hard_edges": self.separate_edges_check.isChecked(),
            "use_normals": self.normals_check.isChecked(),
            "overlap_identical": self.overlap_identical_check.isChecked(),
            "overlap_mirrored": self.overlap_mirrored_check.isChecked(),
            "world_scale": self.world_scale_check.isChecked(),
            "merge_meshes": self.merge_meshes_check.isChecked(),
            "normalize_uv": self.normalize_uv_check.isChecked(),
            "global_pack": self.merge_meshes_check.isChecked(),
            "topology_prefilter_level": self.topology_prefilter_combo.currentData(),
            # Keep the old key for settings written by older GUI versions.
            "topology_prefilter": self.topology_prefilter_combo.currentData() != "off",
        }
        if blender_path:
            values["blender_path"] = os.path.abspath(blender_path)
        try:
            save_settings(values)
        except OSError:
            self._append_activity_log("提示：无法保存 GUI 设置，本次任务不受影响。")

    def _algorithm_changed(self, _index: int) -> None:
        self._algorithm = str(self.algorithm_combo.currentData() or "autouv")
        is_autouv = self._algorithm == "autouv"
        self.autouv_options_group.setVisible(is_autouv)
        self.uniform_options_group.setVisible(not is_autouv)
        self.run_button.setText("开始 AutoUV" if is_autouv else "开始 Uniform UV")
        self.setWindowTitle("Blender AutoUV" if is_autouv else "Blender Uniform UV")
        self._output_suffix = "_autouv" if is_autouv else "_uv"
        self._sync_output_controls()
        self._refresh_autouv_status()
        self._refresh_batch_summary()
        self._refresh_ready_state()

    def _set_all_angles(self, checked: bool) -> None:
        for check in self.angle_checks.values():
            check.setChecked(checked)

    def _open_batch_import(self) -> None:
        if self._output_mode == "path" and not self.output_dir_edit.text().strip():
            QMessageBox.warning(self, "未选择输出目录", "请先选择批量输出目录，再导入待处理文件。")
            return
        dialog = BatchInputDialog(
            self.batch_paths,
            self._algorithm,
            self._output_mode,
            self.output_dir_edit.text().strip() if self._output_mode == "path" else "",
            self._output_suffix if self._output_mode == "suffix" else "",
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.batch_paths = list(dialog.paths)
        self._refresh_batch_summary()
        self._refresh_ready_state()
        self.status_label.setText(f"已准备 {len(self.batch_paths)} 个 FBX 文件，可以开始批处理。")

    def _output_mode_changed(self, checked: bool) -> None:
        if not checked:
            return
        if self.output_source_radio.isChecked():
            self._output_mode = "source"
        elif self.output_suffix_radio.isChecked():
            self._output_mode = "suffix"
        else:
            self._output_mode = "path"
        self._sync_output_controls()
        self._refresh_batch_summary()
        self._refresh_ready_state()

    def _sync_output_controls(self) -> None:
        enabled = self._output_mode == "path"
        running = self.process is not None and self.process.state() != QProcess.NotRunning
        self.output_dir_edit.setEnabled(enabled and not running)
        self.output_dir_browse_button.setEnabled(enabled and not running)

    def _refresh_batch_summary(self) -> None:
        count = len(self.batch_paths)
        self.batch_count_label.setText(f"已选择 {count} 个 FBX 文件")
        self.batch_select_button.setText(
            "编辑批处理文件…" if count else "选择批处理文件…"
        )

    def _refresh_ready_state(self) -> None:
        output_ready = self._output_mode != "path" or bool(self.output_dir_edit.text().strip())
        running = self.process is not None and self.process.state() != QProcess.NotRunning
        self.run_button.setEnabled(bool(self.batch_paths) and output_ready and not running)

    def _refresh_autouv_status(self) -> None:
        if not hasattr(self, "autouv_status_label"):
            return
        if self.udims_spin.value() > 1:
            text = "当前：UDIM>1，逐 Mesh 调用 AutoUV，跳过 UV 归一化"
        elif self.merge_meshes_check.isChecked() and self.normalize_uv_check.isChecked():
            text = "当前：跨 Mesh 合并展开并归一化 UV"
        elif self.merge_meshes_check.isChecked():
            text = "当前：跨 Mesh 合并展开，不归一化 UV"
        elif self.normalize_uv_check.isChecked():
            text = "当前：逐 Mesh 展开并归一化 UV"
        else:
            text = "当前：逐 Mesh 展开，不归一化 UV"
        if (
            self.udims_spin.value() == 1
            and self.world_scale_check.isChecked()
            and self.normalize_uv_check.isChecked()
        ):
            text += "（绝对纹素密度可能改变）"
        self.autouv_status_label.setText(text)

    def _set_workflow_controls_enabled(self, enabled: bool) -> None:
        controls = (
            self.algorithm_combo,
            self.blender_edit,
            self.unwrap_exe_edit,
            self.rotate_combo,
            *self.angle_checks.values(),
            self.resolution_spin,
            self.aspect_spin,
            self.udims_spin,
            self.density_spin,
            self.external_timeout_spin,
            self.parallel_jobs_spin,
            self.separate_edges_check,
            self.normals_check,
            self.overlap_identical_check,
            self.overlap_mirrored_check,
            self.world_scale_check,
            self.merge_meshes_check,
            self.normalize_uv_check,
            self.topology_prefilter_combo,
            self.timeout_spin,
            self.output_source_radio,
            self.output_suffix_radio,
            self.output_dir_radio,
            self.output_dir_edit,
            self.output_dir_browse_button,
            self.batch_select_button,
        )
        for control in controls:
            control.setEnabled(enabled)
        self._sync_output_controls()

    def _browse_output_dir(self) -> None:
        initial = self.output_dir_edit.text().strip() or os.getcwd()
        path = QFileDialog.getExistingDirectory(self, "选择批量输出目录", initial)
        if path:
            self._output_mode = "path"
            self.output_dir_radio.setChecked(True)
            self.output_dir_edit.setText(os.path.abspath(path))
            self._sync_output_controls()
            self._refresh_batch_summary()
            self._refresh_ready_state()

    def _browse_blender(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Blender 可执行文件",
            self.blender_edit.text().strip(),
            "Blender 可执行文件 (blender.exe);;所有文件 (*.*)",
        )
        if path:
            self.blender_edit.setText(path)

    def _browse_unwrap_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 UnWrapConsole3.exe",
            self.unwrap_exe_edit.text().strip(),
            "UnWrapConsole3 (UnWrapConsole3.exe);;所有文件 (*.*)",
        )
        if path:
            self.unwrap_exe_edit.setText(path)

    def _batch_request(self, overwrite: bool):
        angles = [angle for angle, check in self.angle_checks.items() if check.isChecked()] if self._algorithm == "uniform" else []
        return validate_batch_request(
            self.batch_paths,
            self._algorithm,
            self._output_mode,
            self.output_dir_edit.text() if self._output_mode == "path" else "",
            self._output_suffix if self._output_mode == "suffix" else None,
            self.timeout_spin.value(),
            self.external_timeout_spin.value(),
            self.unwrap_exe_edit.text(),
            jobs=self.parallel_jobs_spin.value(),
            resolution=self.resolution_spin.value(),
            separate_hard_edges=self.separate_edges_check.isChecked(),
            aspect=self.aspect_spin.value(),
            use_normals=self.normals_check.isChecked(),
            udims=self.udims_spin.value(),
            overlap_identical=self.overlap_identical_check.isChecked(),
            overlap_mirrored=self.overlap_mirrored_check.isChecked(),
            world_scale=self.world_scale_check.isChecked(),
            density=self.density_spin.value(),
            merge_meshes=self.merge_meshes_check.isChecked(),
            normalize_uv=self.normalize_uv_check.isChecked(),
            topology_prefilter_level=(
                self.topology_prefilter_combo.currentData()
                if self._algorithm == "autouv" else "off"
            ),
            angle_degrees=angles,
            rotate_method=self.rotate_combo.currentData() if self._algorithm == "uniform" else None,
            overwrite=overwrite,
        )

    def _run(self) -> None:
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            return
        if not self.batch_paths:
            QMessageBox.warning(self, "没有待处理文件", "请先选择至少一个 FBX 文件。")
            return
        blender_path = self.blender_edit.text().strip()
        if blender_path and not os.path.isfile(blender_path):
            QMessageBox.warning(self, "Blender 路径无效", "请选择存在的 blender.exe，或清空此项使用自动发现。")
            return
        try:
            request = self._batch_request(overwrite=False)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "参数需要检查", str(exc))
            return

        overwrite = False
        if self.batch_paths:
            planned_outputs = batch_output_paths(request)
            if request.overwrite_source:
                answer = QMessageBox.question(
                    self,
                    "确认批量覆盖源文件",
                    f"将覆盖 {len(self.batch_paths)} 个源 FBX 文件，是否继续？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
            elif any(os.path.exists(path) for path in planned_outputs):
                answer = QMessageBox.question(
                    self,
                    "批量输出文件已存在",
                    f"已有 {sum(os.path.exists(path) for path in planned_outputs)} 个输出文件存在，是否覆盖？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                overwrite = True
                request = self._batch_request(overwrite=True)

        self._save_settings()
        invocation = resolve_cli_invocation()
        self._cancel_dir = tempfile.mkdtemp(prefix="blender-autouv-gui-")
        self._cancel_file = os.path.join(self._cancel_dir, "cancel.marker")
        args = list(invocation.prefix_args) + build_batch_uv_args(
            request,
            cancel_file=self._cancel_file,
        )
        self.process = QProcess(self)
        self.process.setProgram(invocation.program)
        self.process.setArguments(args)
        self.process.setWorkingDirectory(invocation.working_directory)
        environment = QProcessEnvironment.systemEnvironment()
        blender_path = self.blender_edit.text().strip()
        if blender_path:
            environment.insert("CLI_ANYTHING_BLENDER_PATH", os.path.abspath(blender_path))
        environment.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

        self._stdout = ""
        self._stderr = ""
        self._stderr_buffer = ""
        self._cancel_requested = False
        self._force_kill_requested = False
        self._progress_total = len(self.batch_paths)
        self._progress_completed = 0
        self._clear_activity_table()
        self._populate_pending_rows(request)
        self._append_activity_log(f"正在启动 Blender CLI：{invocation.program}")
        algorithm_label = "AutoUV" if self._algorithm == "autouv" else "Uniform UV"
        self.status_label.setText(
            f"处理中：{algorithm_label}（{len(self.batch_paths)} 个文件，并行 {request.jobs}）"
            "导出与校验可能需要一些时间。"
        )
        self.progress.setRange(0, max(self._progress_total, 1))
        self.progress.setValue(0)
        self._set_workflow_controls_enabled(False)
        self.cancel_button.setEnabled(True)
        self.process.start()
        return

    def _clear_activity_table(self) -> None:
        self.activity_table.setRowCount(0)
        self._activity_rows.clear()
        self._activity_log_count = 0

    def _cleanup_cancel_run(self) -> None:
        cancel_dir = self._cancel_dir
        self._cancel_file = None
        self._cancel_dir = None
        if cancel_dir:
            import shutil
            shutil.rmtree(cancel_dir, ignore_errors=True)

    @staticmethod
    def _path_key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def _set_activity_row(
        self,
        input_path: str,
        index: int,
        status: str,
        output_path: str = "",
        detail: str = "",
    ) -> None:
        key = self._path_key(input_path)
        row = self._activity_rows.get(key)
        if row is None:
            row = self.activity_table.rowCount()
            self.activity_table.insertRow(row)
            self._activity_rows[key] = row
        planned_output = output_path
        if not planned_output and self.activity_table.item(row, 3) is not None:
            planned_output = self.activity_table.item(row, 3).text()
        values = (str(index), status, Path(input_path).name, planned_output, detail)
        tooltips = ("", "", os.path.abspath(input_path), planned_output, detail)
        for column, value in enumerate(values):
            item = self.activity_table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.activity_table.setItem(row, column, item)
            item.setText(str(value))
            if tooltips[column]:
                item.setToolTip(tooltips[column])
            item.setBackground(STATUS_COLORS.get(status, QColor("#FFFFFF")))

    def _append_activity_log(self, message: str) -> None:
        self._activity_log_count += 1
        row = self.activity_table.rowCount()
        self.activity_table.insertRow(row)
        values = ("", "日志", "CLI", "", message)
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            item.setBackground(STATUS_COLORS["日志"])
            self.activity_table.setItem(row, column, item)

    def _populate_pending_rows(self, request) -> None:
        for index, input_path in enumerate(request.source_paths, 1):
            output_path = batch_output_paths(request)[index - 1]
            self._set_activity_row(input_path, index, "待处理", output_path, "等待 CLI 开始处理")

    def _read_stdout(self) -> None:
        if self.process is None:
            return
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._stdout += chunk

    def _read_stderr(self) -> None:
        if self.process is None:
            return
        chunk = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_buffer += chunk
        while "\n" in self._stderr_buffer:
            line, self._stderr_buffer = self._stderr_buffer.split("\n", 1)
            self._consume_stderr_line(line.rstrip("\r"))

    def _flush_stderr_buffer(self) -> None:
        if self._stderr_buffer:
            line = self._stderr_buffer
            self._stderr_buffer = ""
            self._consume_stderr_line(line.rstrip("\r"))

    def _consume_stderr_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            event = None
        if isinstance(event, dict) and isinstance(event.get("event"), str):
            self._handle_progress_event(event)
            return
        self._stderr += line + "\n"
        self._append_activity_log(line)

    def _handle_progress_event(self, event: dict) -> None:
        event_name = event.get("event")
        if event_name == "batch_started":
            total = int(event.get("total") or self._progress_total or len(self.batch_paths))
            self._progress_total = max(total, 1)
            self._progress_completed = 0
            self.progress.setRange(0, self._progress_total)
            self.progress.setValue(0)
            jobs = event.get("effective_jobs", event.get("jobs", 1))
            self.status_label.setText(f"批处理开始：共 {total} 个文件，并行 {jobs}。")
            return

        if event_name == "batch_cancel_requested":
            self.status_label.setText("正在取消并清理进程…")
            self._append_activity_log("CLI 已收到取消请求，正在清理活动进程。")
            return

        if event_name == "file_started":
            index = event.get("index", 0)
            total = event.get("total", self._progress_total)
            input_path = str(event.get("input_fbx", ""))
            self.status_label.setText(
                f"处理中：{index}/{total} - {Path(input_path).name}"
            )
            self._set_activity_row(
                input_path,
                int(index or 0),
                "处理中",
                detail=f"正在处理（{index}/{total}）",
            )
            return

        if event_name == "file_finished":
            index = int(event.get("index") or 0)
            total = int(event.get("total") or self._progress_total or 1)
            self._progress_total = max(total, 1)
            completed = int(event.get("completed_count") or 0)
            if completed < 1:
                completed = max(self._progress_completed + 1, index)
            self._progress_completed = max(self._progress_completed, completed)
            self.progress.setRange(0, self._progress_total)
            self.progress.setValue(min(self._progress_completed, self._progress_total))
            self.status_label.setText(
                f"已完成：{self._progress_completed}/{total}；"
                f"运行中：{int(event.get('active_jobs') or 0)} 个"
            )
            input_path = str(event.get("input_fbx", ""))
            name = Path(input_path).name
            output_path = str(event.get("output_fbx") or "")
            duration = event.get("duration_seconds")
            duration_text = (
                f"；耗时 {float(duration):.2f} 秒"
                if isinstance(duration, (int, float)) else ""
            )
            if event.get("cancelled"):
                detail = str(event.get("error") or "文件在批处理取消时未完成")
                self._set_activity_row(
                    input_path, index, "已取消", output_path, detail + duration_text,
                )
            elif event.get("skipped"):
                skip_reason = event.get("skip_reason")
                if skip_reason == "processing_timeout":
                    detail = event.get("error") or (
                        f"单个 FBX 处理超过 {event.get('timeout_seconds', 300)} 秒"
                    )
                    cleanup = event.get("process_cleanup")
                    if cleanup and "process cleanup" not in detail.lower():
                        detail += (
                            "；外部进程树清理"
                            f"{'成功' if cleanup.get('ok') else '失败'}"
                        )
                elif skip_reason == "topology_risk" or event.get("preflight"):
                    preflight = event.get("preflight") or {}
                    reasons = ", ".join(preflight.get("reasons") or [])
                    highest_mesh = next(
                        (
                            mesh for mesh in preflight.get("meshes", [])
                            if mesh.get("object") == preflight.get("highest_risk_mesh")
                        ),
                        {},
                    )
                    metrics = (
                        f"；边界比例 {float(highest_mesh.get('boundary_edge_ratio', 0.0)):.1%}"
                        f"，N-gon 比例 {float(highest_mesh.get('ngon_ratio', 0.0)):.1%}"
                        f"，最大面边数 {highest_mesh.get('max_polygon_vertices', '?')}"
                        if highest_mesh else ""
                    )
                    detail = (
                        f"拓扑风险评分 {preflight.get('risk_score', event.get('risk_score', '?'))} "
                        f"（{preflight.get('risk_level', '高风险')}）"
                        + metrics
                        + (f"；触发：{reasons}" if reasons else "")
                    )
                else:
                    detail = event.get("error") or skip_reason or "未提供原因"
                self._set_activity_row(input_path, index, "跳过", output_path, detail + duration_text)
            elif event.get("ok"):
                warnings = event.get("warnings") or []
                suffix = f"；预警：{' | '.join(warnings)}" if warnings else ""
                self._set_activity_row(
                    input_path, index, "成功", output_path,
                    f"处理完成{suffix}{duration_text}",
                )
            else:
                self._set_activity_row(
                    input_path,
                    index,
                    "失败",
                    output_path,
                    str(event.get("error", "未知错误")) + duration_text,
                )
            return

        if event_name == "batch_finished":
            total = int(event.get("total") or self._progress_total or 1)
            self._progress_total = max(total, 1)
            self._progress_completed = total
            self.progress.setRange(0, self._progress_total)
            self.progress.setValue(self._progress_total)
            self.status_label.setText(
                f"批处理阶段完成：成功 {event.get('success_count', 0)}，"
                f"失败 {event.get('failure_count', 0)}，"
                f"跳过 {event.get('skipped_count', 0)}，"
                f"取消 {event.get('cancelled_count', 0)}，共 {total} 个文件。"
            )

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self.status_label.setText("无法启动 CLI，请检查 Python/CLI 安装。")
            self._append_activity_log("启动失败：请确认 agent-harness 的 .venv 已初始化。")
            self._cleanup_cancel_run()

    def _update_activity_from_result(self, result: dict) -> None:
        for index, item in enumerate(result.get("results") or [], 1):
            input_path = str(item.get("input_fbx") or "")
            if not input_path:
                continue
            preflight = item.get("preflight") or {}
            duration = item.get("duration_seconds")
            duration_text = (
                f"；耗时 {float(duration):.2f} 秒"
                if isinstance(duration, (int, float)) else ""
            )
            if item.get("cancelled"):
                cleanup = item.get("process_cleanup") or []
                cleanup_text = ""
                if cleanup:
                    cleanup_text = f"；清理进程 {len(cleanup)} 个"
                self._set_activity_row(
                    input_path,
                    int(item.get("index") or index),
                    "已取消",
                    str(item.get("output_fbx") or ""),
                    str(item.get("error") or "文件在批处理取消时未完成")
                    + cleanup_text + duration_text,
                )
            elif item.get("skipped"):
                if item.get("skip_reason") == "topology_risk":
                    rules = preflight.get("triggered_rules") or []
                    rule_text = ", ".join(
                        str(rule.get("reason", "")) for rule in rules
                    ) or ", ".join(preflight.get("reasons") or [])
                    detail = (
                        f"拓扑风险评分 {preflight.get('risk_score', item.get('risk_score', '?'))}"
                        f"（{preflight.get('risk_level', '?')}）"
                    )
                    if preflight.get("risk_version") is not None:
                        detail += f"；评分版本 {preflight['risk_version']}"
                    if preflight.get("highest_risk_mesh"):
                        detail += f"；最高风险 Mesh：{preflight['highest_risk_mesh']}"
                    if rule_text:
                        detail += f"；触发：{rule_text}"
                else:
                    detail = str(item.get("error") or item.get("skip_reason") or "未提供原因")
                    cleanup = item.get("process_cleanup") or {}
                    if cleanup:
                        detail += f"；进程清理：{'成功' if cleanup.get('ok') else '失败'}"
                self._set_activity_row(
                    input_path, int(item.get("index") or index), "跳过",
                    str(item.get("output_fbx") or ""), detail + duration_text,
                )
            elif item.get("ok"):
                warnings = item.get("warnings") or []
                detail = "处理完成"
                pipeline = item.get("result") or {}
                if pipeline.get("algorithm") == "autouv":
                    merge_text = "合并调用" if pipeline.get("merge_meshes_applied") else "逐 Mesh 调用"
                    detail += (
                        f"；{merge_text} {pipeline.get('external_call_count', 0)} 次"
                        f"；归一化 {pipeline.get('normalized_meshes', 0)} 个 Mesh"
                    )
                if warnings:
                    detail += "；预警：" + " | ".join(str(value) for value in warnings)
                self._set_activity_row(
                    input_path, int(item.get("index") or index), "成功",
                    str(item.get("output_fbx") or ""), detail + duration_text,
                )
            else:
                self._set_activity_row(
                    input_path, int(item.get("index") or index), "失败",
                    str(item.get("output_fbx") or ""),
                    str(item.get("error") or "未知错误") + duration_text,
                )

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self.process is None:
            return
        self._read_stdout()
        self._read_stderr()
        self._flush_stderr_buffer()
        canceled = self._cancel_requested
        raw_stdout = self._stdout.strip()
        self.progress.setRange(0, max(self._progress_total, 1))
        self.progress.setValue(min(self._progress_completed, max(self._progress_total, 1)))
        self._set_workflow_controls_enabled(True)
        self.cancel_button.setEnabled(False)

        if canceled:
            try:
                result = parse_cli_json(raw_stdout)
            except ValueError:
                result = None
            if isinstance(result, dict) and "results" in result:
                self._update_activity_from_result(result)
                total = int(result.get("total") or self._progress_total or 1)
                self._progress_total = max(total, 1)
                self._progress_completed = total
                self.progress.setRange(0, self._progress_total)
                self.progress.setValue(self._progress_total)
                self.status_label.setText(
                    f"已取消：成功 {result.get('success_count', 0)}，"
                    f"失败 {result.get('failure_count', 0)}，"
                    f"跳过 {result.get('skipped_count', 0)}，"
                    f"取消 {result.get('cancelled_count', 0)}。"
                )
                self._append_activity_log(
                    f"批处理已取消：成功 {result.get('success_count', 0)}，"
                    f"失败 {result.get('failure_count', 0)}，"
                    f"跳过 {result.get('skipped_count', 0)}，"
                    f"取消 {result.get('cancelled_count', 0)}。"
                )
            else:
                status = "已强制终止，无法获得完整 CLI 摘要。" if self._force_kill_requested else "任务已取消。"
                self.status_label.setText(status)
                self._append_activity_log(status)
        elif exit_code != 0:
            try:
                result = parse_cli_json(raw_stdout)
            except ValueError:
                result = None
            if isinstance(result, dict) and "results" in result:
                self._update_activity_from_result(result)
                total = int(result.get("total") or self._progress_total or 1)
                self._progress_total = max(total, 1)
                self._progress_completed = total
                self.progress.setRange(0, self._progress_total)
                self.progress.setValue(self._progress_total)
                failures = int(result.get("failure_count") or 0)
                skipped = int(result.get("skipped_count") or 0)
                cancelled_count = int(result.get("cancelled_count") or 0)
                if result.get("cancelled") or cancelled_count:
                    status = "批处理已取消"
                elif failures:
                    status = f"批处理完成，但有文件失败（退出码 {exit_code}）"
                elif skipped:
                    status = f"批处理完成，但有文件跳过（退出码 {exit_code}）"
                else:
                    status = f"批处理未成功（退出码 {exit_code}）"
                self.status_label.setText(status + "。")
                self._append_activity_log(
                    f"批处理结束：成功 {result.get('success_count', 0)}，"
                    f"失败 {result.get('failure_count', 0)}，"
                    f"跳过 {result.get('skipped_count', 0)}，"
                    f"取消 {result.get('cancelled_count', 0)}，共 {total} 个文件。"
                )
            else:
                message = self._error_message(raw_stdout, self._stderr, exit_code)
                self.status_label.setText("处理失败。")
                self._append_activity_log(message)
        else:
            try:
                result = parse_cli_json(raw_stdout)
            except ValueError as exc:
                self.status_label.setText("CLI 返回结果无法解析。")
                message = self._error_message(raw_stdout, self._stderr, exit_code, parse_error=str(exc))
                self._append_activity_log(message)
            else:
                if "error" in result:
                    message = self._error_message(raw_stdout, self._stderr, exit_code)
                    self.status_label.setText("处理失败。")
                    self._append_activity_log(message)
                else:
                    self._update_activity_from_result(result)
                    total = int(result.get("total") or self._progress_total or 1)
                    self._progress_total = max(total, 1)
                    self._progress_completed = total
                    self.progress.setRange(0, self._progress_total)
                    self.progress.setValue(self._progress_total)
                    self.status_label.setText("处理完成，已通过 FBX 校验。")
                    self._append_activity_log(
                        "AutoUV 完成。" if self._algorithm == "autouv" else "Uniform UV 完成。"
                    )

        self.process.deleteLater()
        self.process = None
        self._cleanup_cancel_run()
        self._refresh_ready_state()
        if self._close_after_process:
            self._close_after_process = False
            self.close()

    @staticmethod
    def _error_message(
        stdout: str,
        stderr: str,
        exit_code: Optional[int] = None,
        parse_error: Optional[str] = None,
    ) -> str:
        prefix = "CLI 执行失败"
        if exit_code is not None:
            prefix += f"（退出码 {exit_code}）"
        try:
            value = parse_cli_json(stdout)
            if isinstance(value.get("error"), str):
                details = value["error"]
            else:
                details = json.dumps(value, ensure_ascii=False, indent=2)
        except ValueError:
            diagnostics = []
            if parse_error:
                diagnostics.append(f"JSON 解析：{parse_error}")
            if stderr.strip():
                diagnostics.append(f"stderr：\n{stderr.strip()}")
            if stdout.strip():
                diagnostics.append(f"stdout：\n{stdout.strip()}")
            details = "\n\n".join(diagnostics) or "未返回错误详情。"
        return f"{prefix}：\n{details}"

    @staticmethod
    def _format_result(result: dict) -> str:
        if "results" in result:
            lines = [
                f"算法：{result.get('algorithm', '')}",
                f"总数：{result.get('total', 0)}",
                f"并行任务数：{result.get('effective_jobs', result.get('jobs', 1))}",
                f"成功：{result.get('success_count', 0)}",
                f"失败：{result.get('failure_count', 0)}",
                f"跳过：{result.get('skipped_count', 0)}",
                f"取消：{result.get('cancelled_count', 0)}",
                "",
                "逐文件结果：",
            ]
            for item in result.get("results", []):
                if item.get("cancelled"):
                    lines.append(
                        f"[已取消] {item.get('input_fbx', '')}\n"
                        f"  {item.get('error') or '文件在批处理取消时未完成'}"
                    )
                elif item.get("skipped"):
                    skip_reason = item.get("skip_reason")
                    if skip_reason == "processing_timeout":
                        cleanup = item.get("process_cleanup") or {}
                        cleanup_text = ""
                        if cleanup:
                            cleanup_text = (
                                "\n  进程清理："
                                f"{'成功' if cleanup.get('ok') else '失败'}"
                                + (f"（{cleanup.get('details')}）" if cleanup.get('details') else "")
                            )
                        lines.append(
                            f"[跳过] {item.get('input_fbx', '')}\n"
                            f"  原因：单个 FBX 处理超过 {item.get('timeout_seconds', 300)} 秒\n"
                            f"  详情：{item.get('error') or '未提供超时阶段'}"
                            f"{cleanup_text}"
                        )
                    else:
                        preflight = item.get("preflight") or {}
                        triggered = preflight.get("triggered_rules") or []
                        reasons = ", ".join(
                            rule.get("reason", "") for rule in triggered
                        ) or ", ".join(preflight.get("reasons", []))
                        lines.append(
                            f"[跳过] {item.get('input_fbx', '')}\n"
                            f"  评分版本：{preflight.get('risk_version', '?')}\n"
                            f"  最高风险 Mesh：{preflight.get('highest_risk_mesh', '?')}\n"
                            f"  拓扑风险评分：{preflight.get('risk_score', '?')} "
                            f"（{preflight.get('risk_level', 'high')}）\n"
                            f"  原因：{reasons or item.get('error') or '达到高风险阈值'}"
                            + (
                                f"\n  耗时：{float(item['duration_seconds']):.2f} 秒"
                                if isinstance(item.get('duration_seconds'), (int, float)) else ""
                            )
                        )
                elif item.get("ok"):
                    warning_lines = item.get("warnings") or []
                    lines.append(
                        f"[成功] {item.get('input_fbx', '')} -> {item.get('output_fbx', '')}"
                        + (f"\n  预警：{' | '.join(warning_lines)}" if warning_lines else "")
                        + (
                            f"\n  耗时：{float(item['duration_seconds']):.2f} 秒"
                            if isinstance(item.get('duration_seconds'), (int, float)) else ""
                        )
                    )
                else:
                    lines.append(
                        f"[失败] {item.get('input_fbx', '')}\n  {item.get('error', '')}"
                        + (
                            f"\n  耗时：{float(item['duration_seconds']):.2f} 秒"
                            if isinstance(item.get('duration_seconds'), (int, float)) else ""
                        )
                    )
            return "\n".join(lines)
        if "auto_uv_options" in result:
            validation = result.get("validation", {})
            lines = [
                f"输出文件：{result.get('output_fbx', '')}",
                f"Blender：{result.get('blender_version', '')}",
                f"网格对象：{result.get('mesh_objects', '')}",
                f"唯一 Mesh：{result.get('unique_mesh_datablocks', '')}",
                f"UV Loop：{result.get('uv_loop_count', '')}",
                f"跨 Mesh 合并调用：{'已执行' if result.get('merge_meshes_applied') else '未执行'}"
                f"（外部调用 {result.get('external_call_count', 0)} 次）",
                f"归一化到 0-1 Tile：{result.get('normalized_meshes', 0)} 个 Mesh",
                f"UV 归一化：{'已执行' if result.get('normalize_uv_applied') else '未执行'}",
                f"活动 UV Map：{', '.join(result.get('active_uv_maps', []))}",
                f"AutoUV 程序：{result.get('external_executable', '')}",
                "",
                f"校验：{'通过' if not validation.get('errors') else '失败'}",
            ]
            warnings = result.get("external_warnings") or []
            if warnings:
                lines.extend(["", "外部程序提示："])
                lines.extend(f"  - {warning}" for warning in warnings)
            errors = validation.get("errors") or []
            if errors:
                lines.extend(f"  - {error}" for error in errors)
            return "\n".join(lines)
        metrics = result.get("selected_metrics", {})
        validation = result.get("validation", {})
        lines = [
            f"输出文件：{result.get('output_fbx', '')}",
            f"Blender：{result.get('blender_version', '')}",
            f"选中角度：{result.get('selected_angle_limit_degrees', '')}°",
            f"网格对象：{result.get('mesh_objects', '')}",
            f"UV Loop：{result.get('uv_loop_count', '')}",
            "",
            "质量指标：",
            f"  stretch_p95: {metrics.get('stretch_p95', '')}",
            f"  stretch_max: {metrics.get('stretch_max', '')}",
            f"  density_log_std: {metrics.get('density_log_std', '')}",
            f"  invalid_triangles: {metrics.get('invalid_triangles', '')}",
            "",
            f"校验：{'通过' if not validation.get('errors') else '失败'}",
        ]
        errors = validation.get("errors") or []
        if errors:
            lines.extend(f"  - {error}" for error in errors)
        return "\n".join(lines)

    def _cancel(self) -> None:
        if self.process is None or self.process.state() == QProcess.NotRunning:
            return
        self._cancel_requested = True
        self.status_label.setText("正在取消并清理进程…")
        self.cancel_button.setEnabled(False)
        if self._cancel_file:
            try:
                Path(self._cancel_file).write_text("cancel\n", encoding="utf-8")
            except OSError as error:
                self._append_activity_log(f"无法写入取消请求：{error}")
        QTimer.singleShot(5000, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self._force_kill_requested = True
            self.status_label.setText("正在强制终止 CLI 及其子进程…")
            pid = int(self.process.processId() or 0)
            if os.name == "nt" and pid:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    self.process.kill()
            else:
                self.process.kill()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            answer = QMessageBox.question(
                self,
                "任务正在运行",
                "关闭窗口会终止当前任务，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._close_after_process = True
            self._cancel()
            event.ignore()
            return
        event.accept()


def main(argv: Optional[list[str]] = None) -> int:
    app = QApplication(argv or sys.argv)
    app.setApplicationName("Blender UV Tools")
    window = UniformUVWindow()
    window.show()
    return app.exec()
