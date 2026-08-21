from __future__ import annotations

from PySide6 import QtWidgets

from fbx_modifier_tool.ui.main_window import MainWindow


def create_application() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])

    app.setApplicationName("FbxModifierTool")
    app.setOrganizationName("DeadTrail")
    return app


def run() -> int:
    app = create_application()
    window = MainWindow()
    window.show()
    return app.exec()
