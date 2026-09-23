@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

title OpenSource Community Crawler DEBUG
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"

echo ============================================================
echo OpenSource Community Crawler DEBUG
echo Folder: %CD%
echo Output: debug_report.txt / debug_results.json
echo ============================================================
echo.

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

echo.
echo [INFO] Running debug_crawler.py...
echo.
python -B debug_crawler.py
set "DEBUG_EXIT=%ERRORLEVEL%"

echo.
echo ============================================================
echo DEBUG EXIT CODE: %DEBUG_EXIT%
echo See:
echo   %CD%\debug_report.txt
echo   %CD%\debug_results.json
echo ============================================================
pause
exit /b %DEBUG_EXIT%
