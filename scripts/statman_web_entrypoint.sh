#!/bin/sh
# Railway Web: public $PORT = same-origin portal (UI + chunked film upload).
#
# Streamlit's own uploader hits Railway 502 (AxiosError) on match films.
# Portal keeps one public port: /api/* → upload API, everything else → Streamlit.
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
export ENJOYSTATS_JOBS_DIR="${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}"
export ENJOYSTATS_FILM_INBOX="${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}"
export ENJOYSTATS_FILM_UPLOADS="${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}"
export ENJOYSTATS_HOST="127.0.0.1"
export ENJOYSTATS_PORT="$API_PORT"
export ENJOYSTATS_API_UPSTREAM="http://127.0.0.1:${API_PORT}"
export ENJOYSTATS_UI_UPSTREAM="http://127.0.0.1:${UI_PORT}"
export ENJOYSTATS_SAME_ORIGIN_UPLOAD=1
export STATMAN_USE_EXTERNAL_WORKER="${STATMAN_USE_EXTERNAL_WORKER:-1}"
export STATMAN_UPLOAD_ONLY=1
export ENJOYSTATS_UVICORN_APP="${ENJOYSTATS_UVICORN_APP:-api.upload_app:app}"
# Chunked upload via portal — do not use Streamlit's axios uploader on Railway.
export STATMAN_STREAMLIT_FILM_UPLOAD=0

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS" \
  "$LOG_DIR" \
  /app/.local-run \
  2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
  chmod -R a+rwX "$DATA_ROOT" /app/.local-run "$LOG_DIR" 2>/dev/null || true
fi

# Prove the Web dyno can write the shared jobs dir (Worker should see this file).
echo "web-boot $(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat" \
  || echo "statman-web: WARNING cannot write $ENJOYSTATS_JOBS_DIR"
echo "statman-web: jobs_dir=$ENJOYSTATS_JOBS_DIR inbox=$ENJOYSTATS_FILM_INBOX"
ls -la "$ENJOYSTATS_JOBS_DIR" 2>/dev/null || true

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

echo "statman-web: portal on 0.0.0.0:${PUBLIC_PORT} (UI ${UI_PORT} · API ${API_PORT})"
echo "statman-web: Domain target port must be exactly: ${PUBLIC_PORT}"

exec python -m api.portal \
  --host 0.0.0.0 \
  --port "$PUBLIC_PORT" \
  --api "$ENJOYSTATS_API_UPSTREAM" \
  --ui "$ENJOYSTATS_UI_UPSTREAM"
