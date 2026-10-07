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
  echo "statman-worker: Worker → Settings → Volumes → mount the SAME volume as Web at /data"
  exit 1
fi

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS"

chmod -R a+rwX "$DATA_ROOT" 2>/dev/null || true

echo "worker-boot $(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname)" \
  >"$ENJOYSTATS_JOBS_DIR/.worker_heartbeat"

echo "statman-worker: jobs=${ENJOYSTATS_JOBS_DIR} inbox=${ENJOYSTATS_FILM_INBOX}"
echo "statman-worker: listing /data and jobs…"
ls -la "$DATA_ROOT" || true
ls -la "$ENJOYSTATS_JOBS_DIR" || true

if [ -f "$ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat" ]; then
  echo "statman-worker: web_heartbeat=yes"
  cat "$ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat" || true
else
  echo "statman-worker: web_heartbeat=NO"
  echo "statman-worker: Web has not written to this volume yet."
  echo "statman-worker: Fix: same volume name on Web+Worker, mount path /data, redeploy WEB first."
fi

exec python -m analytics.collect_worker
