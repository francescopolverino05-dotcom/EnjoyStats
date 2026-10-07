# StatMan on Railway

## You are here because the site says “Application failed to respond”

The app **is** deploying. The public URL is aimed at the **wrong port**.
A tiny test server on the same URL also failed — this is Railway networking,
not StatMan code.

### Do this once (Web service only)

1. **Variables**  
   - Delete `PORT` if you added it by hand.  
   - Save.

2. **Settings → Deploy**  
   - Clear **Custom Start Command** (empty).  
   - Save.

3. **Settings → Networking**  
   - Delete the public domain `statman-production.up.railway.app`.

4. **Deployments → Redeploy** Web. Wait for Success.

5. Open the new deploy → **View logs**. Find this line:  
   `statman-web: Streamlit on 0.0.0.0:NNNN`  
   Remember **NNNN**.

6. **Settings → Networking → Generate Domain**  
   - When it asks for the port, type **NNNN** (same number from the log).  
   - Not 8501 unless the log says 8501.

7. Open the new URL.

### Worker (Analyse / “Queued for StatMan”)

- Config-as-code: `railway.worker.toml` (not `railway.toml`)
- Volume at `/data` (**same** volume as Web)
- No public domain
- Logs must show: `[statman-worker] watching /data/jobs`
- Then after Analyse Stats: `[statman-worker] starting ….status.json`
- If Worker logs `web_heartbeat=NO`, volumes are wrong or Web never wrote.
  Both services need the **same volume name**, mount path exactly `/data`.
  Redeploy **Web first**, then Worker.
- Web deploy must show: `statman-web: wrote heartbeat → /data/jobs/...`
  If Web instead says `FATAL — /data is NOT a Railway volume mount`,
  the Web service still has no volume (or wrong mount path).
- Healthy Worker line: `web_heartbeat=yes`

### Shared env (both)

```
ENJOYSTATS_JOBS_DIR=/data/jobs
ENJOYSTATS_FILM_INBOX=/data/inbox
ENJOYSTATS_FILM_UPLOADS=/data/uploads
```

Web also:

```
STATMAN_USE_EXTERNAL_WORKER=1
ENJOYSTATS_SAME_ORIGIN_UPLOAD=1
```

Film upload: chunked same-origin API through the portal (avoids Streamlit Axios 502).
Domain target port must match logs (`portal on 0.0.0.0:NNNN` — often **8000**).
