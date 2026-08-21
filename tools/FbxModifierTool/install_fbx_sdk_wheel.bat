@echo off
setlocal
cd /d "%~dp0"
set "FBX_SDK_DOWNLOAD_URL=https://aps.autodesk.com/developer/overview/fbx-sdk"

if "%~1"=="" (
  echo Usage:
  echo install_fbx_sdk_wheel.bat "C:\path\to\fbx-xxxx-cp310-win_amd64.whl"
  echo Official download page: %FBX_SDK_DOWNLOAD_URL%
  pause
  exit /b 1
)

if not exist "%~1" (
  echo Wheel file was not found:
  echo %~1
  echo Official download page: %FBX_SDK_DOWNLOAD_URL%
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo .venv was not found. Run init_venv.bat first.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install "%~1"
if errorlevel 1 (
  echo FBX SDK wheel installation failed.
  pause
  exit /b 1
)
python -c "import fbx; print('FBX SDK import succeeded:', getattr(fbx, '__file__', '<unknown>'))"
if errorlevel 1 (
  echo FBX SDK import verification failed.
  pause
  exit /b 1
)

endlocal
