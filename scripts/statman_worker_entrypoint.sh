#!/bin/sh
# Railway mounts /data as root; fix ownership then start the worker.
set -eu

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
mkdir -p \
  "${ENJOYSTATS_JOBS_DIR:-$DATA_ROOT/jobs}" \
  "${ENJOYSTATS_FILM_INBOX:-$DATA_ROOT/inbox}" \
  "${ENJOYSTATS_FILM_UPLOADS:-$DATA_ROOT/uploads}" \
  2>/dev/null || true

# Volume is often root-owned; make it usable for the app user when possible.
if [ "$(id -u)" = "0" ]; then
  chown -R statman:statman "$DATA_ROOT" 2>/dev/null || true
  exec su -s /bin/sh statman -c "exec python -m analytics.collect_worker"
fi

exec python -m analytics.collect_worker
