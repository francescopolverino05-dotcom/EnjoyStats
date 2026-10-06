#!/bin/sh
# Railway Web: Streamlit must listen on the SAME port the public domain targets.
# Mismatch = "Application failed to respond" (HTTP 502) even when deploy is green.
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
# Prefer an explicit override, then Railway PORT, then the domain port we document (8501).
LISTEN_PORT="${STATMAN_LISTEN_PORT:-${PORT:-8501}}"
LOG_DIR="${STATMAN_WEB_LOG_DIR:-/tmp/statman-web}"

export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="/app"
export PORT="${LISTEN_PORT}"
export ENJOYSTATS_JOBS_DIR="${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}"
export ENJOYSTATS_FILM_INBOX="${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}"
export ENJOYSTATS_FILM_UPLOADS="${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}"
export STATMAN_USE_EXTERNAL_WORKER="${STATMAN_USE_EXTERNAL_WORKER:-1}"
export STATMAN_STREAMLIT_FILM_UPLOAD="${STATMAN_STREAMLIT_FILM_UPLOAD:-1}"

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS" \
  "$LOG_DIR" \
  2>/dev/null || true

# Fix volume ownership when possible; stay root for the server so PATH/PORT never get lost under su.
if [ "$(id -u)" = "0" ]; then
  chown -R enjoystats:enjoystats "$DATA_ROOT" 2>/dev/null || true
  chmod -R a+rwX "$DATA_ROOT" 2>/dev/null || true
fi

echo "statman-web: listening on 0.0.0.0:${LISTEN_PORT}"
echo "statman-web: Railway domain target port MUST equal ${LISTEN_PORT}"

# Bind Streamlit before importing the heavy dashboard script path issues matter.
# CLI flags override .streamlit/config.toml port.
exec streamlit run app/dashboard.py \
  --server.address=0.0.0.0 \
  --server.port="${LISTEN_PORT}" \
  --server.headless=true \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.maxUploadSize=5120 \
  --server.maxMessageSize=5120 \
  --browser.gatherUsageStats=false
