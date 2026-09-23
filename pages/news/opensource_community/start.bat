@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

title OpenSource Community Crawler
set "BASE_PORT=8010"
set "MAX_PORT=8020"
set "PORT=%BASE_PORT%"

:CHECK_PORT
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=%PORT%; if ((Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { exit 1 } else { exit 0 }" >nul 2>nul
if errorlevel 1 (
  echo [WARN] Port %PORT% is occupied, trying next port...
  set /a PORT+=1
  if !PORT! GTR %MAX_PORT% (
    echo [ERROR] No free port found in range %BASE_PORT%-%MAX_PORT%.
    pause
    exit /b 1
  )
  goto CHECK_PORT
)

set "OSC_PORT=%PORT%"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"

echo ============================================================
echo OpenSource Community Crawler
echo Folder: %CD%
echo URL:    http://127.0.0.1:%PORT%
echo API:    http://127.0.0.1:%PORT%/api/crawl?refresh=false^&days=7^&fallback_days=15
echo Log:    %CD%\crawler_service.log
echo ============================================================
echo.

echo http://127.0.0.1:%PORT%> crawler_service_url.txt

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment. Make sure Python is installed and added to PATH.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"

if not exist ".deps_installed" (
  echo [INFO] Installing dependencies for first run...
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
  )
  echo installed> .deps_installed
) else (
  echo [INFO] Dependencies already installed. Skipping pip install.
)

echo [INFO] Starting crawler server...
echo [%date% %time%] Starting crawler on port %PORT%>> crawler_service.log
python -B server.py

echo.
echo [WARN] Crawler server exited.
pause
