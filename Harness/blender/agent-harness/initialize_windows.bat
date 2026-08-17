@echo off
setlocal

for /f "tokens=2 delims=:" %%A in ('chcp') do set "ORIGINAL_CODEPAGE=%%A"
chcp 65001 >nul

rem Keep the process rooted at this harness so relative paths and generated
rem files remain local to the copied harness directory.
cd /d "%~dp0"

set "SETUP_SCRIPT=%~dp0initialize_windows.ps1"
if not exist "%SETUP_SCRIPT%" (
    set "EXIT_CODE=1"
    goto :finish
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SETUP_SCRIPT%" "%~1"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    pause >nul
)

:finish
if defined ORIGINAL_CODEPAGE chcp %ORIGINAL_CODEPAGE% >nul
exit /b %EXIT_CODE%
