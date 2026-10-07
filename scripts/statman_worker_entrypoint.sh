#!/bin/sh
# Railway Worker — claim queued Analyse jobs from the shared /data volume.
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="${PYTHONPATH:-/app}"
export ENJOYSTATS_JOBS_DIR="$DATA_ROOT/jobs"
export ENJOYSTATS_FILM_INBOX="$DATA_ROOT/inbox"
export ENJOYSTATS_FILM_UPLOADS="$DATA_ROOT/uploads"
export STATMAN_WORKER_POLL_S="${STATMAN_WORKER_POLL_S:-3}"
export MPLBACKEND="${MPLBACKEND:-Agg}"

echo "statman-worker: checking shared volume at ${DATA_ROOT}"
echo "statman-worker: mounts mentioning data:"
awk '{print "  " $0}' /proc/mounts 2>/dev/null | grep -i data || echo "  (none)"

if ! awk -v root="$DATA_ROOT" '$2 == root { found=1 } END { exit !found }' /proc/mounts \
  && ! awk -v root="$DATA_ROOT" 'index($2, root "/") == 1 { found=1 } END { exit !found }' /proc/mounts; then
  echo "statman-worker: FATAL — ${DATA_ROOT} is NOT a Railway volume mount."
  exit 1
fi

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS"

chmod -R a+rwX "$DATA_ROOT" 2>/dev/null || true

FP_FILE="$DATA_ROOT/STATMAN_VOLUME_FINGERPRINT.txt"
echo "statman-worker: --- volume identity check ---"
if [ -f "$FP_FILE" ]; then
  echo "statman-worker: VOLUME FINGERPRINT (from Web) → $(cat "$FP_FILE")"
else
  echo "statman-worker: VOLUME FINGERPRINT FILE MISSING"
  echo "statman-worker: This Worker /data is NOT the Web /data disk."
  echo "statman-worker: FIX: Worker → Volumes → remove volume → Mount EXISTING → pick Web's volume → path /data"
fi

echo "worker-boot $(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname)" \
  >"$ENJOYSTATS_JOBS_DIR/.worker_heartbeat"

echo "statman-worker: listing ${DATA_ROOT}:"
ls -la "$DATA_ROOT" || true
echo "statman-worker: listing ${ENJOYSTATS_JOBS_DIR}:"
ls -la "$ENJOYSTATS_JOBS_DIR" || true

if [ -f "$ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat" ]; then
  echo "statman-worker: web_heartbeat=yes"
  echo "statman-worker: heartbeat contents → $(cat "$ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat")"
else
  echo "statman-worker: web_heartbeat=NO"
  echo "statman-worker: You almost certainly created TWO volumes (one per service)."
  echo "statman-worker: Both must use one shared volume name, mount path /data."
fi

exec python -m analytics.collect_worker
