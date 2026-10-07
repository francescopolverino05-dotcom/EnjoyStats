# StatMan on Railway

## Analyse stuck on “Queued for StatMan”

Mount path `/data` on two services is not enough — they must share **one**
volume *name*. That kept failing, so **Web now embeds the Analyse worker**
(`STATMAN_EMBED_WORKER=1`). Jobs are claimed on the same disk as uploads.

1. Redeploy **Web**
2. Web logs must show: `statman-web: embedded Analyse worker`
3. After Analyse Stats: `[statman-worker] starting ….status.json`
   (in Web deploy logs / embed-worker.log)

A separate Worker service is optional. If you keep it, it must mount the
**same** volume name as Web at `/data` (not a second new volume).

## Domain / port

- Web logs: `portal on 0.0.0.0:NNNN` → Networking target port = **NNNN**
- Site: https://statman-production.up.railway.app

## Web volume

- Web → Settings → Volumes → mount path exactly `/data`
- Web must start with: `wrote heartbeat → /data/jobs/...`
- If `FATAL — /data is NOT a Railway volume mount`, fix the Web volume first

## Env (Web)

```
STATMAN_USE_EXTERNAL_WORKER=1
STATMAN_EMBED_WORKER=1
ENJOYSTATS_SAME_ORIGIN_UPLOAD=1
```

## Optional dedicated Worker

- Config: `railway.worker.toml`
- Same volume name as Web, path `/data`
- Logs: `web_heartbeat=yes` (if NO, wrong volume — ignore Worker, use embed)
