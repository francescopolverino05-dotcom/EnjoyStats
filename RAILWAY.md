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
#
# Flow: upload film on the site → job queued under /data/jobs →
# worker claims it → Analyse runs off the web dyno → download PDF →
# delete the film when done.

## Web “Application failed to respond” (HTTP 502)
#
# Site: https://statman-production.up.railway.app
#
# Fix the domain target port (most common cause):
#   1. Web → Variables → set PORT = 8501
#   2. Web → Settings → Networking → domain target port = 8501
#      (same number as PORT — not 3000, not blank mismatch)
#   3. Redeploy Web
#   4. Deploy logs must show: statman-web: Streamlit 0.0.0.0:8501
#
# Web start is Streamlit on $PORT only (no portal sidecar).

## Web / Worker config files
#
#   Web    → railway.toml          (Dockerfile.dashboard)
#   Worker → railway.worker.toml   (Dockerfile.worker)

Web start:
  /bin/sh /app/scripts/statman_web_entrypoint.sh

Worker start:
  /app/scripts/statman_worker_entrypoint.sh

Required shared env (both services):
  ENJOYSTATS_JOBS_DIR=/data/jobs
  ENJOYSTATS_FILM_INBOX=/data/inbox
  ENJOYSTATS_FILM_UPLOADS=/data/uploads

Web-only:
  STATMAN_USE_EXTERNAL_WORKER=1
  STATMAN_STREAMLIT_FILM_UPLOAD=1
  PORT=8501

Worker-only:
  STATMAN_WORKER_POLL_S=3

Optional password gate (Streamlit):
  STATMAN_SITE_PASSWORD=your-shared-password
