# STAT MAN / EnjoyStats

Football match auto-tagger and Once Sport Analyser exporter.

**Collect in-house** (no Grokbot): film → smart watch → 5-min re-pass → Review → Home/Away XML.

## Upload a match (one path)

1. Start the stack: `./run_local.sh`  
   - FastAPI on `:8000` is **auto-started and auto-restarted** if it dies  
   - Portal on `:8080` = **one URL** for the UI + film upload (same origin — no `Failed to fetch`)
2. Open **http://localhost:8080** (or the try-link that tunnels the portal)
3. Upload match film → wait for Saved → set kits/line-up → **Analyse Stats**
4. Review tags → download Home.xml / Away.xml

There is no Streamlit film picker, no link paste, and no second uploader.
Analyse always uses the newest film in the inbox.

If you only run Streamlit by hand, the dashboard still calls `ensure_api_running()`
and boots Uvicorn on `:8000` automatically. For phones / try-links, use the portal.

## Railway (website + background worker)

See **[RAILWAY.md](RAILWAY.md)** for the full checklist.

Short version: deploy **two** services from this repo, share one volume at `/data`:

| Service | Role | Start |
|---------|------|--------|
| **Web** | Password site, upload, pick match, download PDF | Streamlit (`Dockerfile.dashboard` / `railway.toml`) |
| **Worker** | Runs Analyse off the web dyno | `python -m analytics.collect_worker` (`Dockerfile.worker`) |

Set `STATMAN_USE_EXTERNAL_WORKER=1` on Web so Analyse is only queued.
The worker claims jobs from `/data/jobs` and writes progress the site already shows.

## Game-week batch

After several films are in the inbox via the same uploader:

1. Upload the **line-up CSV** (names + shirt numbers)
2. Set Home / Away **kit colours**
3. Open **Game-week batch** → **Collect ALL inbox films**
4. When a match finishes → **Review tags** → download Home.xml / Away.xml

### How collect works
1. **Watch** (YOLO people finder + kit colours + jersey OCR when readable)
2. Use your **line-ups** for real player names
3. Split into **5-minute chunks**; thin chunks get denser re-pass
4. **Review tags** — fix only mistakes
5. Export OnceSport Home/Away XMLs

### Accuracy (honest)
Computer watch + line-ups + Review is how you get board-ready sheets.  
Always upload line-ups before a game-week batch.

## Quick start

```bash
pip install -r requirements.txt
# system package: tesseract-ocr (for shirt numbers)
./run_local.sh
# open http://localhost:8080  (portal — UI + always-on upload API)
# or separately:
#   python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
#   streamlit run app/dashboard.py
#   ENJOYSTATS_SAME_ORIGIN_UPLOAD=1 python -m api.portal --port 8080
```

## Monorepo

| Path | Role |
|------|------|
| `api/film_upload.py` | **Only** match-film upload (chunked) |
| `api/supervisor.py` | Auto-start / keep FastAPI running |
| `api/portal.py` | Same-origin UI + upload (kills Failed to fetch) |
| `analytics/lineups.py` | CSV/JSON line-up import |
| `analytics/jersey_ocr.py` | Shirt-number OCR (Tesseract) |
| `analytics/batch_collect.py` | Queue all inbox films |
| `analytics/smart_detect.py` | YOLO / HOG people finder |
| `analytics/block_coverage.py` | 5-minute coverage + re-pass |
| `analytics/review_edits.py` | Review fixes before export |
| `analytics/oncesport_export.py` | Home/Away OnceSport XML |
| `analytics/video_auto_collect.py` | Film → events → XML |
