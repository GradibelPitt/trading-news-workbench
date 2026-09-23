$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

# ============================================================
# 1. Port conflict detection for main 7x24 server (8765+)
# ============================================================
$port = 8765
$maxPort = $port + 10

while ($port -le $maxPort) {
    $inUse = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $inUse) {
        break
    }
    Write-Host "[WARN] Port $port is already in use, trying $($port + 1)..." -ForegroundColor Yellow
    $port++
}

if ($port -gt $maxPort) {
    Write-Host "[ERROR] Could not find a free port in range 8765-$maxPort." -ForegroundColor Red
    exit 1
}

# ============================================================
# 2. Start stock-analysis backend in background (for "关注" tab)
# ============================================================
$stockBat = Join-Path $here 'pages\stock-analysis-page\start.bat'

if (Test-Path $stockBat) {
    Write-Host '[INFO] Launching stock-analysis backend in background...' -ForegroundColor DarkGray
    Start-Process -FilePath 'cmd.exe' `
                  -ArgumentList '/c', "`"$stockBat`"" `
                  -WorkingDirectory (Split-Path $stockBat) `
                  -WindowStyle Hidden
} else {
    Write-Host '[WARN] stock-analysis start.bat not found, skipping.' -ForegroundColor Yellow
}


# ============================================================
# 3. Start opensource-community crawler backend in background
#    Location: pages/news/opensource_community
#    For debugging this launches start.bat in a visible console window.
# ============================================================
$openSourceDir = Join-Path $here 'pages\news\opensource_community'
$openSourceBat = Join-Path $openSourceDir 'start.bat'
$openSourceAlreadyRunning = $false

foreach ($crawlerPort in 8010..8020) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$crawlerPort/health" -TimeoutSec 1 -ErrorAction Stop
        if ($health.app -eq 'AI News Crawler') {
            Write-Host "[INFO] opensource-community crawler already running at http://127.0.0.1:$crawlerPort, skipping launch." -ForegroundColor DarkGray
            $openSourceAlreadyRunning = $true
            break
        }
    } catch {
        # Port not serving this crawler.
    }
}

if (-not $openSourceAlreadyRunning) {
    if (Test-Path $openSourceBat) {
        Write-Host '[INFO] Launching opensource-community crawler backend in a visible debug window...' -ForegroundColor DarkGray
        Start-Process -FilePath 'cmd.exe' `
                      -ArgumentList '/c', "`"$openSourceBat`"" `
                      -WorkingDirectory $openSourceDir `
                      -WindowStyle Normal
    } else {
        Write-Host '[WARN] opensource-community crawler start.bat not found, skipping.' -ForegroundColor Yellow
        Write-Host "       Expected: $openSourceBat" -ForegroundColor DarkGray
    }
}

# ============================================================
# 4. Auto-open browser after server is ready
# ============================================================
$url = "http://127.0.0.1:$port/"
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 2
    Start-Process $using:url
} | Out-Null

# ============================================================
# 5. Start main 7x24 Market Wire server (foreground)
# ============================================================
$env:SERVER_PORT = $port
Write-Host "[INFO] Starting 7x24 Market Wire at $url" -ForegroundColor Cyan
Write-Host "[INFO] Press Ctrl+C to stop." -ForegroundColor DarkGray
python -B .\server.py
