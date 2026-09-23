#!/usr/bin/env bash
# macOS one-click service stopper for 交易资讯站 v1.0.
# Double-click in Finder, or run: ./mac_stop.command

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT_RANGES=("8765-8775" "8000-8020" "18110-18120")
declare -a TARGET_PIDS=()

echo "============================================================"
echo "[GVIW] Stopping macOS services..."
echo "============================================================"

if ! command -v lsof >/dev/null 2>&1; then
  echo "[ERROR] lsof is required but was not found."
  exit 1
fi

pid_is_project_service() {
  local pid="$1" cwd command
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"

  [[ "$cwd" == "$HERE" || "$cwd" == "$HERE"/* ]] && return 0
  [[ "$command" == *"$HERE"* ]] && return 0
  return 1
}

add_pid() {
  local pid="$1" existing
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  for existing in "${TARGET_PIDS[@]:-}"; do
    [[ "$existing" == "$pid" ]] && return 0
  done
  if pid_is_project_service "$pid"; then
    TARGET_PIDS+=("$pid")
  fi
}

for range in "${PORT_RANGES[@]}"; do
  start="${range%-*}"
  end="${range#*-}"
  for ((port=start; port<=end; port++)); do
    while IFS= read -r pid; do
      add_pid "$pid"
    done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  done
done

# Also catch a project service that is starting up but has not bound its port yet.
while IFS= read -r pid; do
  add_pid "$pid"
done < <(pgrep -f "(uvicorn backend\.app:app|pages/news/opensource_community/.+server\.py|[Pp]ython.*-B server\.py)" 2>/dev/null || true)

if ((${#TARGET_PIDS[@]} == 0)); then
  echo "[INFO] No running GVIW services were found."
else
  echo "[INFO] Sending TERM to: ${TARGET_PIDS[*]}"
  kill "${TARGET_PIDS[@]}" 2>/dev/null || true

  for _ in {1..20}; do
    remaining=()
    for pid in "${TARGET_PIDS[@]}"; do
      kill -0 "$pid" 2>/dev/null && remaining+=("$pid")
    done
    ((${#remaining[@]} == 0)) && break
    sleep 0.1
  done

  if ((${#remaining[@]} > 0)); then
    echo "[WARN] Force-stopping remaining processes: ${remaining[*]}"
    kill -KILL "${remaining[@]}" 2>/dev/null || true
  fi
  echo "[OK] GVIW services stopped."
fi

echo "============================================================"
if [[ -t 0 && "${1:-}" != "nopause" ]]; then
  echo "Press any key to close this window."
  IFS= read -r -n 1 -s || true
  echo
fi
