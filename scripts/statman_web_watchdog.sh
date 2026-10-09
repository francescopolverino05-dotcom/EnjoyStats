#!/bin/sh
# Restart Streamlit / upload API if they die (common after Analyse RAM pressure).
set -eu

UI_PORT="${ENJOYSTATS_UI_PORT:-18501}"
API_PORT="${ENJOYSTATS_PORT:-18000}"
LOG_DIR="${STATMAN_WEB_LOG_DIR:-/tmp/statman-web}"
UVICORN_APP="${ENJOYSTATS_UVICORN_APP:-api.upload_app:app}"
INTERVAL="${STATMAN_WATCHDOG_S:-8}"

mkdir -p "$LOG_DIR"
export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="/app"

http_ok() {
  url="$1"
  python -c "import urllib.request; urllib.request.urlopen('$url', timeout=2)" \
    >/dev/null 2>&1
}

start_api() {
  echo "statman-watchdog: starting upload API on 127.0.0.1:${API_PORT}"
  python -m uvicorn "$UVICORN_APP" \
    --host 127.0.0.1 \
    --port "$API_PORT" \
    --log-level warning \
    >>"$LOG_DIR/upload-api.log" 2>&1 &
  echo $! >"$LOG_DIR/upload-api.pid"
}

start_ui() {
  echo "statman-watchdog: starting Streamlit on 127.0.0.1:${UI_PORT}"
  streamlit run app/dashboard.py \
    --server.address=127.0.0.1 \
    --server.port="$UI_PORT" \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.maxUploadSize=5120 \
    --server.maxMessageSize=5120 \
    --browser.gatherUsageStats=false \
    >>"$LOG_DIR/streamlit.log" 2>&1 &
  echo $! >"$LOG_DIR/streamlit.pid"
}

echo "statman-watchdog: watching UI :${UI_PORT} and API :${API_PORT} every ${INTERVAL}s"
while true; do
  if ! http_ok "http://127.0.0.1:${API_PORT}/health"; then
    start_api
    sleep 2
  fi
  if ! http_ok "http://127.0.0.1:${UI_PORT}/_stcore/health"; then
    # Streamlit often dies when Analyse spikes RAM; bring the site back.
    if [ -f "$LOG_DIR/streamlit.pid" ]; then
      old=$(cat "$LOG_DIR/streamlit.pid" 2>/dev/null || true)
      if [ -n "${old:-}" ]; then
        kill "$old" 2>/dev/null || true
      fi
    fi
    start_ui
    # Give Streamlit time to bind before the next check.
    sleep 12
  fi
  sleep "$INTERVAL"
done
