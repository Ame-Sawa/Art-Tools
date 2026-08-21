from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore

from fbx_modifier_tool.services.fbx_service import scan_fbx_folder


class ScanWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, folder_path: str) -> None:
        super().__init__()
        self._folder_path = folder_path

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = scan_fbx_folder(Path(self._folder_path))
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
