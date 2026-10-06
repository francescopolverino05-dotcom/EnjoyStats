#!/bin/sh
# Railway Web: Streamlit on the public $PORT.
#
# Keep this simple — a portal + sidecar API previously caused
# "Application failed to respond" (first request blocked on API boot / port mismatch).
# Film upload uses Streamlit's uploader into /data when STATMAN_USE_EXTERNAL_WORKER=1.
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
PORT="${PORT:-8501}"
LOG_DIR="${STATMAN_WEB_LOG_DIR:-/tmp/statman-web}"

export PATH="/opt/venv/bin:${PATH:-/usr/local/bin:/usr/bin:/bin}"
export PYTHONPATH="${PYTHONPATH:-/app}"
export PORT
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

if [ "$(id -u)" = "0" ]; then
  chown -R enjoystats:enjoystats "$DATA_ROOT" 2>/dev/null || true
  chown -R enjoystats:enjoystats "$LOG_DIR" 2>/dev/null || true
fi

echo "statman-web: Streamlit on 0.0.0.0:${PORT}"

run_streamlit() {
  exec streamlit run app/dashboard.py \
    --server.address=0.0.0.0 \
    --server.port="${PORT}" \
    --server.headless=true \
    --server.maxUploadSize=5120 \
    --server.maxMessageSize=5120 \
    --browser.gatherUsageStats=false
}

if [ "$(id -u)" = "0" ]; then
  exec su -s /bin/sh enjoystats -c "
    export PATH='/opt/venv/bin:/usr/local/bin:/usr/bin:/bin'
    export PYTHONPATH='/app'
    export PORT='${PORT}'
    export ENJOYSTATS_JOBS_DIR='${ENJOYSTATS_JOBS_DIR}'
    export ENJOYSTATS_FILM_INBOX='${ENJOYSTATS_FILM_INBOX}'
    export ENJOYSTATS_FILM_UPLOADS='${ENJOYSTATS_FILM_UPLOADS}'
    export STATMAN_USE_EXTERNAL_WORKER='${STATMAN_USE_EXTERNAL_WORKER}'
    export STATMAN_STREAMLIT_FILM_UPLOAD='${STATMAN_STREAMLIT_FILM_UPLOAD}'
    export STATMAN_SITE_PASSWORD='${STATMAN_SITE_PASSWORD:-}'
    exec streamlit run app/dashboard.py \
      --server.address=0.0.0.0 \
      --server.port=${PORT} \
      --server.headless=true \
      --server.maxUploadSize=5120 \
      --server.maxMessageSize=5120 \
      --browser.gatherUsageStats=false
  "
fi

run_streamlit
