@echo off
title GVIW Service Restarter
cd /d "%~dp0"

echo ==============================================
echo [GVIW] Restarting all GVIW services...
echo ==============================================
echo.

echo [INFO] Step 1: Stopping existing GVIW services...
call stop.bat nopause

echo.
echo [INFO] Step 2: Starting GVIW services...
call start.bat

echo.
echo [GVIW] Restart sequence completed.
echo ==============================================
