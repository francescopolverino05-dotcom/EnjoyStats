# StatMan on Railway — web site + Analyse worker
#
# CRITICAL — Worker must NOT use the Web Dockerfile.
# In Worker → Settings → Build:
#   Dockerfile path = Dockerfile.worker
# Or Config-as-code file = railway.worker.toml
#
# 1. Deploy this GitHub repo as TWO services (Web + Worker).
# 2. Add a Volume in the Railway UI, mount it at /data on BOTH services.
#    (Do not use Dockerfile VOLUME — Railway rejects that instruction.)
# 3. Copy env from .env.example (Railway section) onto both.
# 4. Web: public URL, Dockerfile.dashboard / railway.toml
# 5. Worker: no public domain, Dockerfile.worker / railway.worker.toml
#    start = python -m analytics.collect_worker
#
# Flow: upload film on the site → job queued under /data/jobs →
# worker claims it → Analyse runs off the web dyno → download PDF →
# delete the film when done.

## Web “Application failed to respond”
#
# Site URL (production): https://statman-production.up.railway.app
# Web start is Streamlit on $PORT (no portal sidecar).
# Domain port must be **8501** (or whatever $PORT is).
# Logs should show: `statman-web: Streamlit on 0.0.0.0:…`

## Web / Worker both fail to build or deploy
#
# 1. Each service needs its **own** Config-as-code file:
#      Web    → railway.toml          (Dockerfile.dashboard)
#      Worker → railway.worker.toml   (Dockerfile.worker)
#    If Worker is left on railway.toml it builds the Web image and breaks.
# 2. Generate Service Domain port: **8501** (Web only).
# 3. Attach Volume at /data on both (Railway UI — not Dockerfile VOLUME).
# 4. Redeploy both after this branch updates.

Web start (Railway / Docker):
  /app/scripts/statman_web_entrypoint.sh
  (Streamlit on $PORT · film upload via Streamlit into /data)

Worker start:
  /app/scripts/statman_worker_entrypoint.sh
  (or: python -m analytics.collect_worker)

Worker build:
  Dockerfile path = Dockerfile.worker
  (If Worker crashes instantly, it is almost always still on Dockerfile.dashboard
  OR /data volume permissions — entrypoint now chowns /data.)

Required shared env (both services):
  ENJOYSTATS_JOBS_DIR=/data/jobs
  ENJOYSTATS_FILM_INBOX=/data/inbox
  ENJOYSTATS_FILM_UPLOADS=/data/uploads

Web-only (defaults are set in Dockerfile.dashboard):
  STATMAN_USE_EXTERNAL_WORKER=1
  STATMAN_STREAMLIT_FILM_UPLOAD=1
  PORT=8501   # or Railway's $PORT — must match Generate Service Domain

Worker-only:
  STATMAN_WORKER_POLL_S=3

Optional password gate (Streamlit):
  STATMAN_SITE_PASSWORD=your-shared-password
