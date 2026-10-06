#!/bin/sh
# Temporary diagnostic boot — confirms Railway domain port routing.
# Once https://statman-production.up.railway.app shows "port OK", switch back
# to Streamlit in a follow-up commit.
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="/app"
export PORT="${PORT:-8501}"

mkdir -p \
  "${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}" \
  "${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}" \
  "${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}" \
  2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
  chmod -R a+rwX "$DATA_ROOT" 2>/dev/null || true
fi

echo "statman-web: DIAGNOSTIC server on 0.0.0.0:${PORT}"
exec python /app/scripts/statman_web_diag.py
