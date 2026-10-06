#!/bin/sh
# Railway Web — MUST listen on Railway's $PORT (public proxy + healthchecks use it).
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
# Never ignore Railway's PORT. Domain target port must match this value.
LISTEN_PORT="${PORT:?PORT must be set by Railway (or set PORT=8501 in Variables)}"

export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="/app"
export ENJOYSTATS_JOBS_DIR="${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}"
export ENJOYSTATS_FILM_INBOX="${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}"
export ENJOYSTATS_FILM_UPLOADS="${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}"
export STATMAN_USE_EXTERNAL_WORKER="${STATMAN_USE_EXTERNAL_WORKER:-1}"
export STATMAN_STREAMLIT_FILM_UPLOAD="${STATMAN_STREAMLIT_FILM_UPLOAD:-1}"

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS" \
  2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
  chown -R enjoystats:enjoystats "$DATA_ROOT" 2>/dev/null || true
  chmod -R a+rwX "$DATA_ROOT" 2>/dev/null || true
fi

echo "statman-web: Streamlit 0.0.0.0:${LISTEN_PORT}"
echo "statman-web: set Networking domain target port = ${LISTEN_PORT}"

exec streamlit run app/dashboard.py \
  --server.address=0.0.0.0 \
  --server.port="${LISTEN_PORT}" \
  --server.headless=true \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.maxUploadSize=5120 \
  --server.maxMessageSize=5120 \
  --browser.gatherUsageStats=false
