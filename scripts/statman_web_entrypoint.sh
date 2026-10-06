#!/bin/sh
# Railway Web: one public PORT for UI + film upload.
#
# Streamlit alone cannot serve /upload-film on the public domain (browsers
# cannot reach container :8000). This entrypoint starts Streamlit on an
# internal port, then the same-origin portal on $PORT (portal also starts
# the slim upload API).
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
PORT="${PORT:-8501}"
# Keep Streamlit off the public PORT so portal can bind $PORT.
UI_PORT="${ENJOYSTATS_UI_PORT:-18501}"
API_PORT="${ENJOYSTATS_PORT:-8000}"
LOG_DIR="${STATMAN_WEB_LOG_DIR:-/tmp/statman-web}"

if [ "$UI_PORT" = "$PORT" ]; then
  UI_PORT=18501
fi

mkdir -p \
  "${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}" \
  "${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}" \
  "${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}" \
  "$LOG_DIR" \
  2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
  chown -R enjoystats:enjoystats "$DATA_ROOT" 2>/dev/null || true
  chown -R enjoystats:enjoystats "$LOG_DIR" 2>/dev/null || true
fi

export PORT
export ENJOYSTATS_HOST="${ENJOYSTATS_HOST:-127.0.0.1}"
export ENJOYSTATS_PORT="$API_PORT"
export ENJOYSTATS_API_UPSTREAM="${ENJOYSTATS_API_UPSTREAM:-http://127.0.0.1:${API_PORT}}"
export ENJOYSTATS_UI_UPSTREAM="${ENJOYSTATS_UI_UPSTREAM:-http://127.0.0.1:${UI_PORT}}"
export ENJOYSTATS_SAME_ORIGIN_UPLOAD=1
export STATMAN_UPLOAD_ONLY="${STATMAN_UPLOAD_ONLY:-1}"
export ENJOYSTATS_UVICORN_APP="${ENJOYSTATS_UVICORN_APP:-api.upload_app:app}"
export PYTHONPATH="${PYTHONPATH:-/app}"

echo "statman-web: starting Streamlit on ${UI_PORT}"
if [ "$(id -u)" = "0" ]; then
  su -s /bin/sh enjoystats -c "streamlit run app/dashboard.py \
    --server.address=127.0.0.1 \
    --server.port=${UI_PORT} \
    --server.headless=true \
    --server.maxUploadSize=5120 \
    --server.maxMessageSize=5120 \
    --browser.gatherUsageStats=false \
    >${LOG_DIR}/streamlit.log 2>&1" &
else
  streamlit run app/dashboard.py \
    --server.address=127.0.0.1 \
    --server.port="${UI_PORT}" \
    --server.headless=true \
    --server.maxUploadSize=5120 \
    --server.maxMessageSize=5120 \
    --browser.gatherUsageStats=false \
    >"${LOG_DIR}/streamlit.log" 2>&1 &
fi

i=0
while [ "$i" -lt 60 ]; do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${UI_PORT}/_stcore/health', timeout=1)" \
      >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 0.5
done

echo "statman-web: starting portal on ${PORT} (UI ${UI_PORT} · API ${API_PORT})"
if [ "$(id -u)" = "0" ]; then
  exec su -s /bin/sh enjoystats -c \
    "exec python -m api.portal --host 0.0.0.0 --port ${PORT} --api ${ENJOYSTATS_API_UPSTREAM} --ui ${ENJOYSTATS_UI_UPSTREAM}"
fi
exec python -m api.portal \
  --host 0.0.0.0 \
  --port "$PORT" \
  --api "$ENJOYSTATS_API_UPSTREAM" \
  --ui "$ENJOYSTATS_UI_UPSTREAM"
