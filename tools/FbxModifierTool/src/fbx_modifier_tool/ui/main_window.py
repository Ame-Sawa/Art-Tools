from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from fbx_modifier_tool.models import FbxDocument, FolderScanResult, RuntimeStatus
from fbx_modifier_tool.services.fbx_service import (
    build_default_export_path,
    detect_runtime_status,
    export_fbx_document,
    load_fbx_document,
    rename_material_entry,
    rename_mesh_entry,
)
from fbx_modifier_tool.ui.workers import ScanWorker


class FileListDialog(QtWidgets.QDialog):
    import_requested = QtCore.Signal(int)
    refresh_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FBX 文件列表")
        self.resize(980, 420)
        self.setModal(False)

        self._scan_result: FolderScanResult | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.status_label = QtWidgets.QLabel("尚未扫描文件夹。")
        self.status_label.setStyleSheet("font-weight: 600; color: #1f3b57;")

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        header_layout.addWidget(self.status_label, 1)

        self.refresh_button = QtWidgets.QPushButton("重新扫描")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.refresh_button.setEnabled(False)
        header_layout.addWidget(self.refresh_button)

        hint = QtWidgets.QLabel("点击某一行的“导入”按钮，把该 FBX 拉进主工作区。这个窗口可以和主窗口同时存在。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5b6670;")

        self.file_table = QtWidgets.QTableWidget()
        self.file_table.setColumnCount(4)
        self.file_table.setHorizontalHeaderLabels(["文件名", "绝对路径", "大小(KB)", "导入"])
        self.file_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.file_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.file_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setWordWrap(False)
        self.file_table.setShowGrid(True)
        self.file_table.setGridStyle(QtCore.Qt.SolidLine)
        self.file_table.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f6f9fc; border: 1px solid #d7e1ec; border-radius: 6px; gridline-color: #d7e1ec; selection-background-color: #dbeafe; selection-color: #17324d; }"
            "QTableWidget::item { padding: 6px; border-right: 1px solid #e2e8f0; }"
            "QTableWidget::item:selected { background: #dbeafe; color: #17324d; }"
            "QHeaderView::section { background: #dfe8f2; padding: 7px; border: 1px solid #d7e1ec; font-weight: 600; color: #17324d; }"
        )
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)

        self.setStyleSheet(
            "QDialog { background: #f8fbff; }"
            "QPushButton { background: #e7eef7; border: 1px solid #c8d4e3; border-radius: 6px; padding: 6px 12px; color: #17324d; }"
            "QPushButton:hover { background: #dbe7f4; }"
        )

        layout.addLayout(header_layout)
        layout.addWidget(hint)
        layout.addWidget(self.file_table, 1)

    def set_scan_result(self, result: FolderScanResult) -> None:
        self._scan_result = result
        self.refresh_button.setEnabled(True)

        if not result.success:
            self.status_label.setText("扫描失败。")
            self.file_table.setRowCount(0)
            return

        self.status_label.setText(f"扫描完成：找到 {len(result.items)} 个 FBX 文件。")
        self.file_table.setRowCount(len(result.items))

        for row, item in enumerate(result.items):
            self.file_table.setItem(row, 0, QtWidgets.QTableWidgetItem(item.file_path.name))
            self.file_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(item.file_path)))
            self.file_table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{item.file_size / 1024:.1f}"))

            import_button = QtWidgets.QPushButton("导入")
            import_button.setMinimumWidth(72)
            import_button.clicked.connect(lambda _checked=False, row_index=row: self.import_requested.emit(row_index))
            self.file_table.setCellWidget(row, 3, import_button)

        self.file_table.resizeRowsToContents()
        self.file_table.verticalHeader().setDefaultSectionSize(32)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FBX Modifier Tool")
        self.resize(1180, 760)

        self._document: FbxDocument | None = None
        self._scan_result: FolderScanResult | None = None
        self._scan_thread: QtCore.QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._file_list_dialog = FileListDialog(self)
        self._file_list_dialog.import_requested.connect(self._import_from_scan_row)
        self._file_list_dialog.refresh_requested.connect(self._refresh_scan)

        self._build_ui()
        self._load_runtime_status()

    def _build_ui(self) -> None:
        central_widget = QtWidgets.QWidget(self)
        self.setCentralWidget(central_widget)

        root_layout = QtWidgets.QVBoxLayout(central_widget)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(12)

        root_layout.addWidget(self._create_path_group())
        root_layout.addWidget(self._create_editor_group(), 1)
        root_layout.addWidget(self._create_log_group(), 1)

    def _create_path_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("导入导出设置")
        layout = QtWidgets.QGridLayout(group)

        self.scan_folder_edit = QtWidgets.QLineEdit()
        self.scan_folder_edit.setPlaceholderText("选择包含 FBX 的文件夹，选完后自动扫描并弹出文件列表")

        scan_folder_button = QtWidgets.QPushButton("浏览...")
        scan_folder_button.clicked.connect(self._browse_scan_folder)

        self.import_edit = QtWidgets.QLineEdit()
        self.import_edit.setReadOnly(True)
        self.import_edit.setPlaceholderText("从文件列表窗口点击某一行的“导入”按钮")
        self.import_edit.setStyleSheet("background: #f7fafc; color: #203040;")

        self.export_edit = QtWidgets.QLineEdit()
        self.export_edit.setPlaceholderText("选择导出 FBX 文件路径")
        self.export_edit.setStyleSheet("background: #ffffff; color: #203040;")

        export_button = QtWidgets.QPushButton("浏览...")
        export_button.clicked.connect(self._browse_export_file)

        self.file_list_button = QtWidgets.QPushButton("文件列表")
        self.file_list_button.clicked.connect(self._show_file_list_dialog)
        self.file_list_button.setEnabled(False)

        self.refresh_scan_button = QtWidgets.QPushButton("刷新列表")
        self.refresh_scan_button.clicked.connect(self._refresh_scan)
        self.refresh_scan_button.setEnabled(False)

        self.save_button = QtWidgets.QPushButton("导出 FBX")
        self.save_button.clicked.connect(self._export_document)

        self.outline_vertex_color_checkbox = QtWidgets.QCheckBox("导出时把平滑法线写入顶点色")
        self.outline_vertex_color_checkbox.setChecked(True)
        self.outline_vertex_color_checkbox.setToolTip("按顶点位置聚合并平均法线，编码到顶点色 RGB，供壳体描边读取。")

        self.workspace_label = QtWidgets.QLabel("当前工作区：未加载文件")
        self.workspace_label.setStyleSheet("font-weight: 600; color: #17324d;")

        self.document_status_label = QtWidgets.QLabel("尚未导入 FBX。选择文件夹后会自动扫描，并弹出文件列表窗口。")
        self.document_status_label.setWordWrap(True)
        self.document_status_label.setStyleSheet("color: #425466;")

        group.setStyleSheet(
            "QGroupBox { font-weight: 600; border: 1px solid #cfd8e3; border-radius: 8px; margin-top: 8px; background: #f8fbff; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #17324d; }"
            "QLineEdit { border: 1px solid #c8d4e3; border-radius: 6px; padding: 6px 8px; }"
            "QPushButton { background: #e7eef7; border: 1px solid #c8d4e3; border-radius: 6px; padding: 6px 12px; color: #17324d; }"
            "QPushButton:hover { background: #dbe7f4; }"
        )

        layout.addWidget(QtWidgets.QLabel("扫描文件夹"), 0, 0)
        layout.addWidget(self.scan_folder_edit, 0, 1)
        layout.addWidget(scan_folder_button, 0, 2)
        layout.addWidget(QtWidgets.QLabel("选择后自动扫描"), 0, 3)

        layout.addWidget(QtWidgets.QLabel("导入路径"), 1, 0)
        layout.addWidget(self.import_edit, 1, 1)
        layout.addWidget(QtWidgets.QLabel("从文件列表窗口点击导入"), 1, 3)

        layout.addWidget(QtWidgets.QLabel("导出路径"), 2, 0)
        layout.addWidget(self.export_edit, 2, 1)
        layout.addWidget(export_button, 2, 2)

        export_actions = QtWidgets.QHBoxLayout()
        export_actions.addWidget(self.outline_vertex_color_checkbox)
        export_actions.addStretch(1)
        export_actions.addWidget(self.file_list_button)
        export_actions.addWidget(self.refresh_scan_button)
        export_actions.addWidget(self.save_button)
        layout.addLayout(export_actions, 2, 3)

        layout.addWidget(self.workspace_label, 3, 0, 1, 4)
        layout.addWidget(self.document_status_label, 4, 0, 1, 4)
        return group

    def _create_editor_group(self) -> QtWidgets.QTabWidget:
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._create_mesh_tab(), "Mesh")
        tabs.addTab(self._create_material_tab(), "Material")
        return tabs

    def _create_mesh_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        hint = QtWidgets.QLabel("Mesh 重命名会自动补上 Mesh_ 前缀。当前工作区一次只编辑 1 个 FBX，修改只会写到导出文件。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5b6670;")

        self.mesh_summary_label = QtWidgets.QLabel("尚未加载 Mesh 数据。")
        self.mesh_summary_label.setStyleSheet("font-weight: 600; color: #1f3b57;")

        self.mesh_table = QtWidgets.QTableWidget()
        self.mesh_table.setColumnCount(5)
        self.mesh_table.setHorizontalHeaderLabels(["原始名称", "待导出名称", "定位路径", "重命名", "快捷设为 Main"])
        self.mesh_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.mesh_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.mesh_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.mesh_table.setAlternatingRowColors(True)
        self.mesh_table.verticalHeader().setVisible(False)
        self.mesh_table.setWordWrap(False)
        self.mesh_table.setShowGrid(True)
        self.mesh_table.setGridStyle(QtCore.Qt.SolidLine)
        self.mesh_table.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f4f7fa; gridline-color: #d7e1ec; selection-background-color: #dbeafe; selection-color: #17324d; }"
            "QTableWidget::item { padding: 6px; border-right: 1px solid #e2e8f0; }"
            "QTableWidget::item:selected { background: #dbeafe; color: #17324d; }"
            "QHeaderView::section { background: #e9eef4; padding: 6px; border: 1px solid #d7e1ec; font-weight: 600; color: #17324d; }"
        )
        header = self.mesh_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)

        layout.addWidget(hint)
        layout.addWidget(self.mesh_summary_label)
        layout.addWidget(self.mesh_table, 1)
        tab.setStyleSheet(
            "QTableWidget { border: 1px solid #d7e1ec; border-radius: 6px; }"
            "QPushButton { background: #edf4fb; border: 1px solid #c8d4e3; border-radius: 6px; padding: 5px 10px; color: #17324d; }"
            "QPushButton:hover { background: #dfeaf7; }"
        )
        return tab

    def _create_material_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        hint = QtWidgets.QLabel("Material 重命名会在导出时写入新 FBX，不会原地修改源文件。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5b6670;")

        self.material_summary_label = QtWidgets.QLabel("尚未加载 Material 数据。")
        self.material_summary_label.setStyleSheet("font-weight: 600; color: #1f3b57;")

        self.material_table = QtWidgets.QTableWidget()
        self.material_table.setColumnCount(4)
        self.material_table.setHorizontalHeaderLabels(["原始名称", "待导出名称", "重命名", "快捷设为 Main"])
        self.material_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.material_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.material_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.material_table.setAlternatingRowColors(True)
        self.material_table.verticalHeader().setVisible(False)
        self.material_table.setWordWrap(False)
        self.material_table.setShowGrid(True)
        self.material_table.setGridStyle(QtCore.Qt.SolidLine)
        self.material_table.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f4f7fa; gridline-color: #d7e1ec; selection-background-color: #dbeafe; selection-color: #17324d; }"
            "QTableWidget::item { padding: 6px; border-right: 1px solid #e2e8f0; }"
            "QTableWidget::item:selected { background: #dbeafe; color: #17324d; }"
            "QHeaderView::section { background: #e9eef4; padding: 6px; border: 1px solid #d7e1ec; font-weight: 600; color: #17324d; }"
        )
        header = self.material_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)

        layout.addWidget(hint)
        layout.addWidget(self.material_summary_label)
        layout.addWidget(self.material_table, 1)
        tab.setStyleSheet(
            "QTableWidget { border: 1px solid #d7e1ec; border-radius: 6px; }"
            "QPushButton { background: #edf4fb; border: 1px solid #c8d4e3; border-radius: 6px; padding: 5px 10px; color: #17324d; }"
            "QPushButton:hover { background: #dfeaf7; }"
        )
        return tab

    def _create_log_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("状态与日志")
        layout = QtWidgets.QVBoxLayout(group)

        self.runtime_label = QtWidgets.QLabel("")
        self.runtime_label.setWordWrap(True)

        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        group.setStyleSheet(
            "QGroupBox { font-weight: 600; border: 1px solid #cfd8e3; border-radius: 8px; margin-top: 8px; background: #fbfcfe; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #17324d; }"
            "QPlainTextEdit { background: white; border: 1px solid #d7e1ec; border-radius: 6px; color: #203040; }"
        )

        layout.addWidget(self.runtime_label)
        layout.addWidget(self.log_edit, 1)
        return group

    def _load_runtime_status(self) -> None:
        status = detect_runtime_status()
        self._render_runtime_status(status)

    def _render_runtime_status(self, status: RuntimeStatus) -> None:
        self.runtime_label.setText(
            " | ".join(
                [
                    f"Python: {status.python_version}",
                    f"PySide6: {'可用' if status.pyside6_available else '缺失'}",
                    f"FBX SDK: {'可用' if status.fbx_available else '缺失'}",
                ]
            )
        )

        if status.messages:
            for message in status.messages:
                self._append_log(message)

    def _browse_scan_folder(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择 FBX 文件夹",
            self.scan_folder_edit.text().strip() or str(Path.home()),
        )
        if selected:
            self.scan_folder_edit.setText(selected)
            self._start_scan()

    def _browse_export_file(self) -> None:
        selected, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "选择导出 FBX 文件",
            self.export_edit.text().strip() or str(Path.home() / "modified.fbx"),
            "FBX Files (*.fbx)",
        )
        if selected:
            self.export_edit.setText(selected)

    def _load_document(self) -> None:
        import_text = self.import_edit.text().strip()
        export_text = self.export_edit.text().strip()

        if not import_text:
            self._append_log("请先选择导入 FBX 文件。")
            return

        export_path = Path(export_text) if export_text else None
        document = load_fbx_document(Path(import_text), export_path)
        self._document = document
        document.write_smoothed_normals_to_vertex_color = self.outline_vertex_color_checkbox.isChecked()

        if not document.success:
            self.workspace_label.setText("当前工作区：未加载文件")
            self.document_status_label.setText("导入失败。")
            for error in document.errors:
                self._append_log(error)
            self._clear_tables()
            return

        self.workspace_label.setText(f"当前工作区：{document.import_path.name}")
        self.document_status_label.setText(
            f"已导入：{document.import_path.name} | Mesh {len(document.mesh_entries)} 个 | Material {len(document.material_entries)} 个"
        )
        self._populate_mesh_table()
        self._populate_material_table()

        for warning in document.warnings:
            self._append_log(warning)

        self._append_log(f"导入完成：{document.import_path}")

    def _export_document(self) -> None:
        if self._document is None or not self._document.success:
            self._append_log("请先导入一个有效的 FBX 文件。")
            return

        export_text = self.export_edit.text().strip()
        if not export_text:
            self._append_log("请先选择导出路径。")
            return

        export_path = Path(export_text)
        overwrite = False
        self._document.write_smoothed_normals_to_vertex_color = self.outline_vertex_color_checkbox.isChecked()

        if export_path.exists():
            answer = QtWidgets.QMessageBox.question(
                self,
                "确认覆盖",
                f"导出文件已存在，是否覆盖？\n{export_path}",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                self._append_log("已取消导出。")
                return
            overwrite = True

        result = export_fbx_document(self._document, export_path, overwrite=overwrite)

        if result.success:
            self._append_log(
                f"导出完成：{result.export_path} | Mesh 重命名 {result.mesh_rename_count} 个 | Material 重命名 {result.material_rename_count} 个 | 顶点色写入 {result.vertex_color_mesh_count} 个 Mesh"
            )
            self.document_status_label.setText(f"导出完成：{result.export_path}")
        else:
            self._append_log("导出失败。")

        for warning in result.warnings:
            self._append_log(warning)
        for error in result.errors:
            self._append_log(error)

    def _populate_mesh_table(self) -> None:
        document = self._document
        if document is None:
            self.mesh_table.setRowCount(0)
            self.mesh_summary_label.setText("尚未加载 Mesh 数据。")
            return

        self.mesh_table.setRowCount(len(document.mesh_entries))
        self.mesh_summary_label.setText(f"当前共 {len(document.mesh_entries)} 个 Mesh。点击“重命名”可修改待导出名称。")
        for row, entry in enumerate(document.mesh_entries):
            self.mesh_table.setItem(row, 0, QtWidgets.QTableWidgetItem(entry.original_name))
            self.mesh_table.setItem(row, 1, QtWidgets.QTableWidgetItem(entry.current_name))
            self.mesh_table.setItem(row, 2, QtWidgets.QTableWidgetItem(entry.node_path))

            rename_button = QtWidgets.QPushButton("重命名")
            rename_button.setMinimumWidth(88)
            rename_button.clicked.connect(lambda _checked=False, row_index=row: self._rename_mesh(row_index))
            self.mesh_table.setCellWidget(row, 3, rename_button)

            quick_main_button = QtWidgets.QPushButton("设为 Main")
            quick_main_button.setMinimumWidth(96)
            quick_main_button.clicked.connect(lambda _checked=False, row_index=row: self._rename_mesh_to_main(row_index))
            self.mesh_table.setCellWidget(row, 4, quick_main_button)

            if entry.current_name != entry.original_name:
                self._highlight_changed_cell(self.mesh_table.item(row, 1))

        self.mesh_table.resizeRowsToContents()
        self.mesh_table.verticalHeader().setDefaultSectionSize(34)

    def _populate_material_table(self) -> None:
        document = self._document
        if document is None:
            self.material_table.setRowCount(0)
            self.material_summary_label.setText("尚未加载 Material 数据。")
            return

        self.material_table.setRowCount(len(document.material_entries))
        self.material_summary_label.setText(
            f"当前共 {len(document.material_entries)} 个 Material。点击“重命名”可修改待导出名称。"
        )
        for row, entry in enumerate(document.material_entries):
            self.material_table.setItem(row, 0, QtWidgets.QTableWidgetItem(entry.original_name))
            self.material_table.setItem(row, 1, QtWidgets.QTableWidgetItem(entry.current_name))

            rename_button = QtWidgets.QPushButton("重命名")
            rename_button.setMinimumWidth(88)
            rename_button.clicked.connect(lambda _checked=False, row_index=row: self._rename_material(row_index))
            self.material_table.setCellWidget(row, 2, rename_button)

            quick_main_button = QtWidgets.QPushButton("设为 Main")
            quick_main_button.setMinimumWidth(96)
            quick_main_button.clicked.connect(lambda _checked=False, row_index=row: self._rename_material_to_main(row_index))
            self.material_table.setCellWidget(row, 3, quick_main_button)

            if entry.current_name != entry.original_name:
                self._highlight_changed_cell(self.material_table.item(row, 1))

        self.material_table.resizeRowsToContents()
        self.material_table.verticalHeader().setDefaultSectionSize(34)

    def _rename_mesh(self, row_index: int) -> None:
        if self._document is None:
            return

        entry = self._document.mesh_entries[row_index]
        new_name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "重命名 Mesh",
            "输入新的 Mesh 名称：",
            text=entry.current_name.removeprefix("Mesh_"),
        )
        if not accepted:
            return

        try:
            rename_mesh_entry(self._document, row_index, new_name)
        except Exception as exc:
            self._append_log(str(exc))
            return

        self.mesh_table.item(row_index, 1).setText(self._document.mesh_entries[row_index].current_name)
        self._highlight_changed_cell(self.mesh_table.item(row_index, 1))
        self._append_log(
            f"Mesh 已设置导出名称：{entry.original_name} -> {self._document.mesh_entries[row_index].current_name}"
        )

    def _rename_mesh_to_main(self, row_index: int) -> None:
        if self._document is None:
            return

        entry = self._document.mesh_entries[row_index]
        try:
            rename_mesh_entry(self._document, row_index, "Main")
        except Exception as exc:
            self._append_log(str(exc))
            return

        self.mesh_table.item(row_index, 1).setText(self._document.mesh_entries[row_index].current_name)
        self._highlight_changed_cell(self.mesh_table.item(row_index, 1))
        self._append_log(
            f"Mesh 已快捷设为 Main：{entry.original_name} -> {self._document.mesh_entries[row_index].current_name}"
        )

    def _rename_material(self, row_index: int) -> None:
        if self._document is None:
            return

        entry = self._document.material_entries[row_index]
        new_name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "重命名 Material",
            "输入新的 Material 名称：",
            text=entry.current_name,
        )
        if not accepted:
            return

        try:
            rename_material_entry(self._document, row_index, new_name)
        except Exception as exc:
            self._append_log(str(exc))
            return

        self.material_table.item(row_index, 1).setText(self._document.material_entries[row_index].current_name)
        self._highlight_changed_cell(self.material_table.item(row_index, 1))
        self._append_log(
            f"Material 已设置导出名称：{entry.original_name} -> {self._document.material_entries[row_index].current_name}"
        )

    def _rename_material_to_main(self, row_index: int) -> None:
        if self._document is None:
            return

        entry = self._document.material_entries[row_index]
        try:
            rename_material_entry(self._document, row_index, "Main")
        except Exception as exc:
            self._append_log(str(exc))
            return

        self.material_table.item(row_index, 1).setText(self._document.material_entries[row_index].current_name)
        self._highlight_changed_cell(self.material_table.item(row_index, 1))
        self._append_log(
            f"Material 已快捷设为 Main：{entry.original_name} -> {self._document.material_entries[row_index].current_name}"
        )

    def _clear_tables(self) -> None:
        self.mesh_table.setRowCount(0)
        self.material_table.setRowCount(0)
        self.mesh_summary_label.setText("尚未加载 Mesh 数据。")
        self.material_summary_label.setText("尚未加载 Material 数据。")

    def _start_scan(self) -> None:
        folder_text = self.scan_folder_edit.text().strip()
        if not folder_text:
            self._append_log("请先选择要扫描的文件夹。")
            return
        if self._scan_thread is not None:
            self._append_log("当前正在扫描，请稍候。")
            return

        self._set_scan_controls_enabled(False)
        self.document_status_label.setText("扫描中，将在完成后自动弹出文件列表窗口。")
        self._append_log(f"开始扫描文件夹：{folder_text}")

        self._scan_thread = QtCore.QThread(self)
        self._scan_worker = ScanWorker(folder_text)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._cleanup_scan_thread)
        self._scan_thread.start()

    def _on_scan_finished(self, result: FolderScanResult) -> None:
        self._scan_result = result
        self.file_list_button.setEnabled(result.success)
        self.refresh_scan_button.setEnabled(result.success)
        self._file_list_dialog.set_scan_result(result)

        if not result.success:
            self.document_status_label.setText("扫描失败。")
            for error in result.errors:
                self._append_log(error)
            self._show_file_list_dialog()
            return

        self.document_status_label.setText(f"扫描完成：找到 {len(result.items)} 个 FBX 文件。文件列表窗口已打开。")
        self.import_edit.clear()
        self._show_file_list_dialog()

        for warning in result.warnings:
            self._append_log(warning)

        self._append_log(f"扫描完成：{result.folder_path}")

    def _on_scan_failed(self, message: str) -> None:
        self.document_status_label.setText("扫描失败。")
        self._append_log(f"扫描异常：{message}")

    def _cleanup_scan_thread(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.deleteLater()
        if self._scan_thread is not None:
            self._scan_thread.deleteLater()
        self._scan_worker = None
        self._scan_thread = None
        self._set_scan_controls_enabled(True)

    def _import_from_scan_row(self, row_index: int) -> None:
        if self._scan_result is None or not self._scan_result.success:
            return
        if row_index < 0 or row_index >= len(self._scan_result.items):
            return

        selected_item = self._scan_result.items[row_index]
        self._file_list_dialog.file_table.selectRow(row_index)
        self.import_edit.setText(str(selected_item.file_path))
        self.export_edit.setText(str(build_default_export_path(selected_item.file_path)))
        if self._document is not None and selected_item.file_path.resolve() != self._document.import_path.resolve():
            self.workspace_label.setText("当前工作区：等待重新导入")
            self.document_status_label.setText("已选择新的 FBX。点击文件列表中的“导入”按钮后会替换当前工作区内容。")
        self._load_document()

    def _show_file_list_dialog(self) -> None:
        self._file_list_dialog.show()
        self._file_list_dialog.raise_()
        self._file_list_dialog.activateWindow()

    def _refresh_scan(self) -> None:
        if not self.scan_folder_edit.text().strip():
            self._append_log("请先选择要扫描的文件夹。")
            return
        self._append_log("手动刷新文件列表。")
        self._start_scan()

    def _set_scan_controls_enabled(self, enabled: bool) -> None:
        self.refresh_scan_button.setEnabled(enabled and self._scan_result is not None and self._scan_result.success)
        self.file_list_button.setEnabled(enabled and self._scan_result is not None and self._scan_result.success)
        self._file_list_dialog.refresh_button.setEnabled(enabled and bool(self.scan_folder_edit.text().strip()))

    def _highlight_changed_cell(self, item: QtWidgets.QTableWidgetItem | None) -> None:
        if item is None:
            return
        item.setBackground(self.palette().alternateBase())
        item.setForeground(self.palette().text())

    def _append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(message)
