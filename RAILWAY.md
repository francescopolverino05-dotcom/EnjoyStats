# StatMan on Railway — web site + Analyse worker
#
# CRITICAL — Worker must NOT use the Web Dockerfile.
# In Worker → Settings → Build:
#   Dockerfile path = Dockerfile.worker
# Or Config-as-code file = railway.worker.toml
#
# 1. Deploy this GitHub repo as TWO services (Web + Worker).
# 2. Add a Volume, mount it at /data on BOTH services.
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
# Generate Service Domain port: **8501**
# Web entrypoint listens on $PORT with the same-origin **portal**
# (Streamlit UI + /upload-film on one public URL).
# Redeploy Web after pulling the fix that ships `api/` + portal entrypoint.
#
# If it still fails: Web → Deployments → View logs
#   Look for "statman-web: starting portal" and Streamlit health.

Web start (Railway / Docker):
  /app/scripts/statman_web_entrypoint.sh
  (portal on $PORT · Streamlit internal · slim upload API on :8000)

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
  STATMAN_UPLOAD_ONLY=1
  ENJOYSTATS_SAME_ORIGIN_UPLOAD=1
  PORT=8501   # or Railway's $PORT — must match Generate Service Domain

Worker-only:
  STATMAN_WORKER_POLL_S=3

Optional password gate (Streamlit):
  STATMAN_SITE_PASSWORD=your-shared-password
