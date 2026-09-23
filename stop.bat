@echo off
title GVIW Service Stopper
echo ==============================================
echo [GVIW] Stopping services...
echo ==============================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports = (8765..8775) + (8000..8010) + (8010..8020); Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*server.py*' -or $_.CommandLine -like '*backend.app*' -or $_.CommandLine -like '*uvicorn*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Get-Process | Where-Object { $_.MainWindowTitle -like '*OpenSource Community Crawler*' -or $_.MainWindowTitle -like '*GVIW*' } | Stop-Process -Force -ErrorAction SilentlyContinue"

echo [GVIW] Services stopped.
echo ==============================================
if "%1"=="nopause" goto end
pause
:end
