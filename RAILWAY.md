# StatMan on Railway

## Analyse stuck on “Queued for StatMan”

Mount path `/data` on two services is not enough — they must share **one**
volume *name*. That kept failing, so **Web now embeds the Analyse worker**
(`STATMAN_EMBED_WORKER=1`). Jobs are claimed on the same disk as uploads.

1. Redeploy **Web**
2. Web logs must show: `statman-web: embedded Analyse worker`
3. After Analyse Stats: `[statman-worker] starting ….status.json`
   (in Web Deploy Logs — worker output is teed to stdout)
4. Stuck bar (e.g. 29% for hours): redeploy Web — worker re-queues jobs with
   no progress for 15+ minutes. Check `https://…/api/v1/jobs` for state/age.

A separate Worker service is optional. If you keep it, it must mount the
**same** volume name as Web at `/data` (not a second new volume).

## Domain / port

- Site: https://statman.up.railway.app
- Web **Deploy Logs** (not Build Logs) must say: `statman-web: boot begin` then `portal on 0.0.0.0:NNNN`
- Clear the log search box — “no logs found on this filter” means the search text matched nothing
- Networking → domain **target port** must be **that same NNNN**
- Variables → set `PORT=8080` if the domain target is 8080 (or leave PORT unset and set the domain target to whatever Railway assigned)
- Mismatch = “Application failed to respond” even when deploy is Success
- Custom Start Command empty is OK (`railway.toml` / image CMD starts the entrypoint)

## Web volume

- Web → Settings → Volumes → mount path exactly `/data`
- Web must start with: `wrote heartbeat → /data/jobs/...`
- If `WARN — /data is NOT a Railway volume mount`, fix the Web volume (site can still boot)

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
