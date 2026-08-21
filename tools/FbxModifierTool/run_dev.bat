@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" launcher.py
  if errorlevel 1 (
    echo.
    echo Launch failed. Check the error message above.
    pause
  )
) else (
  echo Local virtual environment was not found: %~dp0.venv
  echo Run init_venv.bat first.
  pause
)

endlocal
