@echo off
setlocal EnableExtensions
cd /d "%~dp0" || goto :fail

set "FBX_SDK_DOWNLOAD_URL=https://aps.autodesk.com/developer/overview/fbx-sdk"

echo [1/4] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 64-bit and add it to PATH.
  goto :fail
)
python --version
if errorlevel 1 goto :fail

if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create the virtual environment.
    goto :fail
  )
) else (
  echo [2/4] Using existing virtual environment.
)

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment Python was not found: %CD%\.venv\Scripts\python.exe
  goto :fail
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
  echo Failed to activate the virtual environment.
  goto :fail
)

echo [3/4] Installing Python dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install -r requirements.txt pytest
if errorlevel 1 goto :fail
python -m pip install -e .
if errorlevel 1 goto :fail

if not "%~1"=="" (
  echo [4/4] Installing Autodesk FBX SDK wheel...
  if not exist "%~1" (
    echo FBX SDK wheel was not found: %~1
    goto :fail
  )
  python -m pip install "%~1"
  if errorlevel 1 goto :fail
  python -c "import fbx; print('FBX SDK import succeeded:', getattr(fbx, '__file__', '<unknown>'))"
  if errorlevel 1 goto :fail
) else (
  echo [4/4] Skipping Autodesk FBX SDK installation.
  echo Official download page: %FBX_SDK_DOWNLOAD_URL%
  echo After downloading a wheel, run:
  echo init_venv.bat "C:\path\to\fbx-sdk.whl"
)

echo.
echo Virtual environment is ready.
echo Activate with: call .venv\Scripts\activate.bat
pause
exit /b 0

:fail
echo.
echo Initialization failed. Review the error message above.
pause
exit /b 1
