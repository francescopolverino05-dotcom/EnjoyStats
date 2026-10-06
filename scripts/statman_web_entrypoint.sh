#!/bin/sh
# Railway Web — listen on Railway-injected $PORT only.
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="/app"
export ENJOYSTATS_JOBS_DIR="${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}"
export ENJOYSTATS_FILM_INBOX="${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}"
export ENJOYSTATS_FILM_UPLOADS="${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}"
export STATMAN_USE_EXTERNAL_WORKER="${STATMAN_USE_EXTERNAL_WORKER:-1}"
export STATMAN_STREAMLIT_FILM_UPLOAD="${STATMAN_STREAMLIT_FILM_UPLOAD:-1}"

# Railway injects PORT. Do not hardcode a different port in the domain settings.
if [ -z "${PORT:-}" ]; then
  echo "statman-web: ERROR — PORT is empty. In Railway Variables, delete any custom PORT,"
  echo "statman-web: redeploy, read PORT from logs, then set the domain target to that number."
  exit 1
fi

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS" \
  2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
  chmod -R a+rwX "$DATA_ROOT" 2>/dev/null || true
fi

echo "statman-web: Streamlit on 0.0.0.0:${PORT}"
echo "statman-web: Domain target port must be exactly: ${PORT}"

exec streamlit run app/dashboard.py \
  --server.address=0.0.0.0 \
  --server.port="${PORT}" \
  --server.headless=true \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.maxUploadSize=5120 \
  --server.maxMessageSize=5120 \
  --browser.gatherUsageStats=false
