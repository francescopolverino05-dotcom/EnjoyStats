#!/bin/sh
# Railway Worker — claim queued Analyse jobs from the shared /data volume.
# Run as root (same as Web) so PATH/PYTHONPATH and volume permissions stay intact.
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="${PYTHONPATH:-/app}"
export ENJOYSTATS_JOBS_DIR="${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}"
export ENJOYSTATS_FILM_INBOX="${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}"
export ENJOYSTATS_FILM_UPLOADS="${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}"
export STATMAN_WORKER_POLL_S="${STATMAN_WORKER_POLL_S:-3}"
export MPLBACKEND="${MPLBACKEND:-Agg}"

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS" \
  2>/dev/null || true

# Railway volumes are often root-owned; make them world-writable for Web+Worker.
chmod -R a+rwX "$DATA_ROOT" 2>/dev/null || true

echo "statman-worker: jobs=${ENJOYSTATS_JOBS_DIR} inbox=${ENJOYSTATS_FILM_INBOX}"
echo "statman-worker: listing queued status files…"
ls -la "$ENJOYSTATS_JOBS_DIR" 2>/dev/null || echo "statman-worker: jobs dir missing or empty"

exec python -m analytics.collect_worker
