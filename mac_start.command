#!/usr/bin/env bash
# macOS one-click launcher for 交易资讯站 v1.0.
# Double-click this file in Finder, or run: ./mac_start.command

set -Eeuo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAIN_PORT=8765
MAIN_MAX_PORT=8775
STOCK_PORT=8000
CRAWLER_PORT=8010
CRAWLER_MAX_PORT=8020

cleanup() {
  echo
  echo "[INFO] Stopping child services..."
  for pid in "${STOCK_PID:-}" "${CRAWLER_PID:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

require_python() {
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "[ERROR] Python 3 was not found. Install Python 3, then try again."
    exit 1
  fi
}

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

find_free_port() {
  local port="$1" max_port="$2"
  while (( port <= max_port )); do
    if ! port_in_use "$port"; then
      echo "$port"
      return 0
    fi
    echo "[WARN] Port $port is occupied, trying the next port..." >&2
    ((port++))
  done
  return 1
}

setup_venv() {
  local directory="$1" requirements="$2"
  local venv="$directory/.venv"
  local marker="$venv/.deps_installed"

  if [[ ! -x "$venv/bin/python" ]]; then
    echo "[INFO] Creating virtual environment: $directory/.venv"
    "$PYTHON_BIN" -m venv "$venv"
  fi

  if [[ ! -f "$marker" || "$requirements" -nt "$marker" ]]; then
    echo "[INFO] Installing dependencies for $(basename "$directory")..."
    "$venv/bin/python" -m pip install --upgrade pip
    "$venv/bin/python" -m pip install -r "$requirements"
    touch "$marker"
  fi
}

require_python
export PYTHONDONTWRITEBYTECODE=1
export PYTHONIOENCODING=utf-8

echo "============================================================"
echo "[GVIW] Starting services..."
echo "============================================================"

# Stock-analysis is proxied by the main server at its fixed port, 8000.
STOCK_DIR="$HERE/pages/stock-analysis-page"
if port_in_use "$STOCK_PORT"; then
  echo "[INFO] Port $STOCK_PORT is already in use; keeping the existing stock-analysis service."
else
  setup_venv "$STOCK_DIR" "$STOCK_DIR/backend/requirements.txt"
  echo "[INFO] Launching stock-analysis backend at http://127.0.0.1:$STOCK_PORT"
  (
    cd "$STOCK_DIR"
    exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port "$STOCK_PORT"
  ) >"$STOCK_DIR/stock_analysis_service.log" 2>&1 &
  STOCK_PID=$!
fi

CRAWLER_DIR="$HERE/pages/news/opensource_community"
if ! CRAWLER_PORT="$(find_free_port "$CRAWLER_PORT" "$CRAWLER_MAX_PORT")"; then
  echo "[ERROR] No free port found in range 8010-$CRAWLER_MAX_PORT."
  exit 1
fi
setup_venv "$CRAWLER_DIR" "$CRAWLER_DIR/requirements.txt"
echo "http://127.0.0.1:$CRAWLER_PORT" > "$CRAWLER_DIR/crawler_service_url.txt"
echo "[INFO] Launching OpenSource Community Crawler at http://127.0.0.1:$CRAWLER_PORT"
(
  cd "$CRAWLER_DIR"
  OSC_PORT="$CRAWLER_PORT" exec .venv/bin/python -B server.py
) >"$CRAWLER_DIR/crawler_service.log" 2>&1 &
CRAWLER_PID=$!

if ! MAIN_PORT="$(find_free_port "$MAIN_PORT" "$MAIN_MAX_PORT")"; then
  echo "[ERROR] No free port found in range 8765-$MAIN_MAX_PORT."
  exit 1
fi

URL="http://127.0.0.1:$MAIN_PORT/"
echo "[INFO] Opening $URL"
( sleep 2; open "$URL" ) &

echo "[INFO] Starting 7x24 Market Wire at $URL"
echo "[INFO] Press Control-C to stop services."
SERVER_PORT="$MAIN_PORT" "$PYTHON_BIN" -B server.py
