@echo off
setlocal
for /f "tokens=2 delims=:" %%A in ('chcp') do set "ORIGINAL_CODEPAGE=%%A"
chcp 65001 >nul
set "HARNESS_ROOT=%~dp0"
set "PYTHON_EXE=%HARNESS_ROOT%.venv\Scripts\python.exe"
set "EXIT_CODE=0"

if not exist "%PYTHON_EXE%" (
    echo 未找到虚拟环境：%PYTHON_EXE%
    echo 请先运行 initialize_windows.bat 完成 CLI 初始化。
    set "EXIT_CODE=1"
    goto :finish
)

"%PYTHON_EXE%" -m pip show PySide6 >nul 2>&1
if errorlevel 1 (
    echo 正在安装 GUI 依赖 PySide6...
    "%PYTHON_EXE%" -m pip install --disable-pip-version-check "PySide6>=6.6"
    if errorlevel 1 (
        echo PySide6 安装失败。
        set "EXIT_CODE=1"
        goto :finish
    )
)

pushd "%HARNESS_ROOT%"
"%PYTHON_EXE%" -m cli_anything.blender_gui
set "EXIT_CODE=%ERRORLEVEL%"
popd

:finish
if not "%EXIT_CODE%"=="0" (
    echo.
    echo GUI 启动失败，按任意键关闭窗口。
    pause >nul
)
if defined ORIGINAL_CODEPAGE chcp %ORIGINAL_CODEPAGE% >nul
exit /b %EXIT_CODE%
