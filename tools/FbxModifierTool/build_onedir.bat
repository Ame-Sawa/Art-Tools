@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Local virtual environment was not found: %~dp0.venv
  echo Run init_venv.bat first.
  exit /b 1
)

".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --name FbxModifierTool ^
  --paths src ^
  --paths .. ^
  --hidden-import fbx ^
  --hidden-import PySide6.QtCore ^
  --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  launcher.py

endlocal
