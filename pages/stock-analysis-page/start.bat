@echo off
setlocal enabledelayedexpansion

REM One-click start for stock analysis FastAPI server.
REM Auto-creates venv and installs dependencies if missing.

cd /d %~dp0

REM --- Auto-setup: create venv + install deps if needed ---
if not exist ".venv\Scripts\activate.bat" (
  echo [INFO] Virtual environment not found, creating .venv...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    exit /b 1
  )
  echo [INFO] .venv created.
)

call ".venv\Scripts\activate.bat"

REM Install dependencies if marker file doesn't exist or requirements changed
set "MARKER=.venv\.deps_installed"
set "REQS=backend\requirements.txt"

if not exist "!MARKER!" (
  set NEED_INSTALL=1
) else (
  REM Re-install if requirements.txt is newer than marker
  for %%A in ("!REQS!") do set REQS_TIME=%%~tA
  for %%A in ("!MARKER!") do set MARKER_TIME=%%~tA
  if "!REQS_TIME!" gtr "!MARKER_TIME!" (
    set NEED_INSTALL=1
  ) else (
    set NEED_INSTALL=0
  )
)

if "!NEED_INSTALL!"=="1" (
  echo [INFO] Installing dependencies from !REQS!...
  pip install -q -r "!REQS!"
  if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
  )
  echo. > "!MARKER!"
  echo [INFO] Dependencies installed.
)

if not exist "backend\app.py" (
  echo [ERROR] backend\app.py not found.
  exit /b 1
)

REM --- Port conflict detection: find a free port starting from 8000 ---
set PORT=8000
set /a MAX_PORT=PORT+10

:check_port
if !PORT! gtr !MAX_PORT! (
  echo [ERROR] Could not find a free port in range 8000-!MAX_PORT!.
  exit /b 1
)

netstat -ano | findstr "LISTENING" | findstr ":!PORT! " >nul 2>&1
if !errorlevel! equ 0 (
  echo [WARN] Port !PORT! is already in use, trying !PORT!+1...
  set /a PORT+=1
  goto :check_port
)

echo [INFO] Starting stock analysis page at http://127.0.0.1:!PORT!
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port !PORT!

endlocal
