@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0start.ps1" (
    echo [ERROR] start.ps1 not found in this folder.
    echo Please put start.bat and start.ps1 in the same directory.
    pause
    exit /b 1
)

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo [ERROR] powershell.exe not found.
    pause
    exit /b 1
)

echo [INFO] Launching start.ps1...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo [ERROR] start.ps1 exited with code %EXITCODE%.
) else (
    echo [INFO] Server process exited.
)
pause
exit /b %EXITCODE%
