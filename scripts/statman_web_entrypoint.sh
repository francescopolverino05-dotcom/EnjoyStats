#!/bin/sh
# Railway Web: portal on $PORT + embedded Analyse worker (same disk as uploads).
#
# A separate Worker service is optional. Shared volumes kept failing for us
# (web_heartbeat=NO). Embedding the worker here makes Analyse claim jobs on
# the same filesystem the site writes to.
set -eu

# First line must always appear in Railway Deploy Logs (not Build Logs).
echo "statman-web: boot begin pid=$$ PORT=${PORT:-unset} hostname=$(hostname 2>/dev/null || echo '?')"

DATA_ROOT="${STATMAN_DATA_ROOT:-/data}"
PUBLIC_PORT="${PORT:?PORT must be set by Railway — set Variables PORT to match Networking domain target}"
UI_PORT="${ENJOYSTATS_UI_PORT:-18501}"
API_PORT="${ENJOYSTATS_PORT:-18000}"
LOG_DIR="${STATMAN_WEB_LOG_DIR:-/tmp/statman-web}"
EMBED_WORKER="${STATMAN_EMBED_WORKER:-1}"

if [ "$UI_PORT" = "$PUBLIC_PORT" ]; then UI_PORT=18501; fi
if [ "$API_PORT" = "$PUBLIC_PORT" ]; then API_PORT=18000; fi
if [ "$API_PORT" = "$UI_PORT" ]; then API_PORT=18000; fi

export PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="/app"
export ENJOYSTATS_JOBS_DIR="$DATA_ROOT/jobs"
export ENJOYSTATS_FILM_INBOX="$DATA_ROOT/inbox"
export ENJOYSTATS_FILM_UPLOADS="$DATA_ROOT/uploads"
export ENJOYSTATS_HOST="127.0.0.1"
export ENJOYSTATS_PORT="$API_PORT"
export ENJOYSTATS_API_UPSTREAM="http://127.0.0.1:${API_PORT}"
export ENJOYSTATS_UI_UPSTREAM="http://127.0.0.1:${UI_PORT}"
export ENJOYSTATS_SAME_ORIGIN_UPLOAD=1
export STATMAN_USE_EXTERNAL_WORKER=1
export STATMAN_UPLOAD_ONLY=1
export ENJOYSTATS_UVICORN_APP="${ENJOYSTATS_UVICORN_APP:-api.upload_app:app}"
export STATMAN_STREAMLIT_FILM_UPLOAD=0
export STATMAN_WORKER_POLL_S="${STATMAN_WORKER_POLL_S:-3}"
export MPLBACKEND="${MPLBACKEND:-Agg}"
# Embed Web dyno: no fork (OOM). Keep YOLO — OpenCV 5 often has no HOGDescriptor.
export STATMAN_COLLECT_INPROCESS="${STATMAN_COLLECT_INPROCESS:-1}"
# Accuracy knobs (needs ≥8 GB Web RAM). Lower Hz = more false goals.
export STATMAN_SAMPLE_HZ="${STATMAN_SAMPLE_HZ:-6}"
export STATMAN_MAX_SIDE="${STATMAN_MAX_SIDE:-800}"
export STATMAN_DISABLE_YOLO="${STATMAN_DISABLE_YOLO:-0}"
export STATMAN_MAX_SAMPLE_FRAMES="${STATMAN_MAX_SAMPLE_FRAMES:-54000}"
export STATMAN_MAX_GOALS="${STATMAN_MAX_GOALS:-8}"
# Keep OpenCV / BLAS / torch from spawning thread storms on a small dyno.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export TORCH_NUM_THREADS="${TORCH_NUM_THREADS:-1}"
export CV_NUM_THREADS="${CV_NUM_THREADS:-1}"
echo "statman-web: collect knobs inprocess=${STATMAN_COLLECT_INPROCESS} hz=${STATMAN_SAMPLE_HZ} side=${STATMAN_MAX_SIDE} yolo_off=${STATMAN_DISABLE_YOLO}"

echo "statman-web: checking volume at ${DATA_ROOT}"
awk '{print "  " $0}' /proc/mounts 2>/dev/null | grep -i data || echo "  (none)"

VOLUME_OK=0
if awk -v root="$DATA_ROOT" '$2 == root { found=1 } END { exit !found }' /proc/mounts \
  || awk -v root="$DATA_ROOT" 'index($2, root "/") == 1 { found=1 } END { exit !found }' /proc/mounts; then
  VOLUME_OK=1
fi

# Do not exit here — a hard exit crash-loops and Railway only shows 502 with
# no portal line. Keep serving HTTP; warn loudly if /data is not a volume.
if [ "$VOLUME_OK" != "1" ]; then
  echo "statman-web: WARN — ${DATA_ROOT} is NOT a Railway volume mount (ephemeral disk)."
  echo "statman-web: Web → Settings → Volumes → mount path = /data  (then Redeploy)"
fi

mkdir -p \
  "$ENJOYSTATS_JOBS_DIR" \
  "$ENJOYSTATS_FILM_INBOX" \
  "$ENJOYSTATS_FILM_UPLOADS" \
  "$LOG_DIR" \
  /app/.local-run

chmod -R a+rwX "$DATA_ROOT" "$LOG_DIR" /app/.local-run 2>/dev/null || true

# Do not use bash-only RANDOM here: /bin/sh (dash) + set -u crashes the container.
FP="web host=$(hostname) utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) rnd=$$-$(date -u +%s) vol=${VOLUME_OK}"
echo "$FP" >"$DATA_ROOT/STATMAN_VOLUME_FINGERPRINT.txt"
echo "$FP" >"$ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat"
echo "statman-web: wrote heartbeat → $ENJOYSTATS_JOBS_DIR/.web_enqueue_heartbeat"

echo "statman-web: upload API on 127.0.0.1:${API_PORT}"
python -m uvicorn "$ENJOYSTATS_UVICORN_APP" \
  --host 127.0.0.1 \
  --port "$API_PORT" \
  --log-level warning \
  >"$LOG_DIR/upload-api.log" 2>&1 &
echo $! >"$LOG_DIR/upload-api.pid"

echo "statman-web: Streamlit on 127.0.0.1:${UI_PORT}"
streamlit run app/dashboard.py \
  --server.address=127.0.0.1 \
  --server.port="$UI_PORT" \
  --server.headless=true \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.maxUploadSize=5120 \
  --server.maxMessageSize=5120 \
  --browser.gatherUsageStats=false \
  >"$LOG_DIR/streamlit.log" 2>&1 &
echo $! >"$LOG_DIR/streamlit.pid"

if [ "$EMBED_WORKER" = "1" ] || [ "$EMBED_WORKER" = "true" ] || [ "$EMBED_WORKER" = "yes" ]; then
  echo "statman-web: embedded Analyse worker on ${ENJOYSTATS_JOBS_DIR}"
  # Tee so Railway Deploy Logs show [statman-worker] lines (not only a file under /tmp).
  python -m analytics.collect_worker \
    2>&1 | tee -a "$LOG_DIR/embed-worker.log" &
  echo "statman-web: embed-worker logger pid $! (also: $LOG_DIR/embed-worker.log)"
fi

# Keep Streamlit/API alive after Analyse RAM spikes kill them.
if [ -x /app/scripts/statman_web_watchdog.sh ] || [ -f /app/scripts/statman_web_watchdog.sh ]; then
  /bin/sh /app/scripts/statman_web_watchdog.sh >>"$LOG_DIR/watchdog.log" 2>&1 &
  echo "statman-web: watchdog pid $! (restarts UI/API if they die)"
fi

i=0
while [ "$i" -lt 90 ]; do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${UI_PORT}/_stcore/health', timeout=1)" \
      >/dev/null 2>&1 \
     && python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${API_PORT}/health', timeout=1)" \
      >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 0.5
done

echo "statman-web: portal on 0.0.0.0:${PUBLIC_PORT}"
echo "statman-web: Networking → domain target port MUST be ${PUBLIC_PORT} (else Application failed to respond)"
echo "statman-web: healthcheck http://0.0.0.0:${PUBLIC_PORT}/readyz"
exec python -m api.portal \
  --host 0.0.0.0 \
  --port "$PUBLIC_PORT" \
  --api "$ENJOYSTATS_API_UPSTREAM" \
  --ui "$ENJOYSTATS_UI_UPSTREAM"
