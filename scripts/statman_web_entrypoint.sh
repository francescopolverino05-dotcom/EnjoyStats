#!/bin/sh
# Railway Web: public $PORT = same-origin portal (UI + chunked film upload).
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
PUBLIC_PORT="${PORT:?PORT must be set by Railway}"
UI_PORT="${ENJOYSTATS_UI_PORT:-18501}"
API_PORT="${ENJOYSTATS_PORT:-18000}"
LOG_DIR="${STATMAN_WEB_LOG_DIR:-/tmp/statman-web}"

if [ "$UI_PORT" = "$PUBLIC_PORT" ]; then UI_PORT=18501; fi
if [ "$API_PORT" = "$PUBLIC_PORT" ]; then API_PORT=18000; fi
if [ "$API_PORT" = "$UI_PORT" ]; then API_PORT=18000; fi

export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="/app"
# Always use the shared volume paths (ignore stale Variable overrides off /data).
export ENJOYSTATS_JOBS_DIR="$DATA_ROOT/jobs"
export ENJOYSTATS_FILM_INBOX="$DATA_ROOT/inbox"
export ENJOYSTATS_FILM_UPLOADS="$DATA_ROOT/uploads"
export ENJOYSTATS_HOST="127.0.0.1"
export ENJOYSTATS_PORT="$API_PORT"
export ENJOYSTATS_API_UPSTREAM="http://127.0.0.1:${API_PORT}"
export ENJOYSTATS_UI_UPSTREAM="http://127.0.0.1:${UI_PORT}"
export ENJOYSTATS_SAME_ORIGIN_UPLOAD=1
export STATMAN_USE_EXTERNAL_WORKER=1
export STATMAN_UPLOAD_ONLY=1
export ENJOYSTATS_UVICORN_APP="${ENJOYSTATS_UVICORN_APP:-api.upload_app:app}"
export STATMAN_STREAMLIT_FILM_UPLOAD=0

echo "statman-web: checking shared volume at ${DATA_ROOT}"
echo "statman-web: mounts mentioning data:"
awk '{print "  " $0}' /proc/mounts 2>/dev/null | grep -i data || echo "  (none)"

# mkdir alone is not enough — without a volume, /data is just empty container disk
# and the Worker will never see Web's files (web_heartbeat=NO).
if ! awk -v root="$DATA_ROOT" '$2 == root { found=1 } END { exit !found }' /proc/mounts \
  && ! awk -v root="$DATA_ROOT" 'index($2, root "/") == 1 { found=1 } END { exit !found }' /proc/mounts; then
  echo "statman-web: FATAL — ${DATA_ROOT} is NOT a Railway volume mount."
  echo "statman-web: Web → Settings → Volumes → add volume, mount path = /data"
  echo "statman-web: Use the SAME volume name on the Worker service."
  exit 1
fi

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS" \
  "$LOG_DIR" \
  /app/.local-run

chmod -R a+rwX "$DATA_ROOT" "$LOG_DIR" /app/.local-run 2>/dev/null || true

HB="$ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat"
echo "web-boot $(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname)" >"$HB"
echo "statman-web: wrote heartbeat → $HB"
echo "statman-web: jobs_dir=$ENJOYSTATS_JOBS_DIR inbox=$ENJOYSTATS_FILM_INBOX"
ls -la "$ENJOYSTATS_JOBS_DIR"

echo "statman-web: upload API on 127.0.0.1:${API_PORT}"
python -m uvicorn "$ENJOYSTATS_UVICORN_APP" \
  --host 127.0.0.1 \
  --port "$API_PORT" \
  --log-level warning \
  >"$LOG_DIR/upload-api.log" 2>&1 &

echo "statman-web: Streamlit on 127.0.0.1:${UI_PORT}"
streamlit run app/dashboard.py \
  --server.address=127.0.0.1 \
  --server.port="$UI_PORT" \
  --server.headless=true \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.maxUploadSize=5120 \
  --server.maxMessageSize=5120 \
  --browser.gatherUsageStats=false \
  >"$LOG_DIR/streamlit.log" 2>&1 &

i=0
while [ "$i" -lt 90 ]; do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${UI_PORT}/_stcore/health', timeout=1)" \
      >/dev/null 2>&1 \
     && python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${API_PORT}/health', timeout=1)" \
      >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 0.5
done

# Refresh heartbeat after services are up (Worker may have started earlier).
echo "web-ready $(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname)" >"$HB"

echo "statman-web: portal on 0.0.0.0:${PUBLIC_PORT} (UI ${UI_PORT} · API ${API_PORT})"
echo "statman-web: Domain target port must be exactly: ${PUBLIC_PORT}"

exec python -m api.portal \
  --host 0.0.0.0 \
  --port "$PUBLIC_PORT" \
  --api "$ENJOYSTATS_API_UPSTREAM" \
  --ui "$ENJOYSTATS_UI_UPSTREAM"
