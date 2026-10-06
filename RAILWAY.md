# StatMan on Railway — web site + Analyse worker
#
# 1. New Railway project → deploy this GitHub repo twice (two services).
# 2. Add a Volume, mount it at /data on BOTH services.
# 3. Copy env from .env.example (Railway section) onto both.
# 4. Web service: public URL, start = Streamlit (see railway.toml).
# 5. Worker service: no public domain, Dockerfile = Dockerfile.worker
#    start = python -m analytics.collect_worker
#
# Flow: upload film on the site → job queued under /data/jobs →
# worker claims it → Analyse runs off the web dyno → download PDF →
# delete the film when done.

Web start (Nixpacks / custom):
  streamlit run app/dashboard.py --server.address=0.0.0.0 --server.port=$PORT --server.headless=true --server.maxUploadSize=5120 --browser.gatherUsageStats=false

Worker start:
  python -m analytics.collect_worker

Required shared env (both services):
  ENJOYSTATS_JOBS_DIR=/data/jobs
  ENJOYSTATS_FILM_INBOX=/data/inbox
  ENJOYSTATS_FILM_UPLOADS=/data/uploads

Web-only:
  STATMAN_USE_EXTERNAL_WORKER=1
  PORT=8501   # or Railway's $PORT

Worker-only:
  STATMAN_WORKER_POLL_S=3

Optional password gate (Streamlit):
  STATMAN_SITE_PASSWORD=your-shared-password
