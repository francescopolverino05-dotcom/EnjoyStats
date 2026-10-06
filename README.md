# STAT MAN / EnjoyStats

Football match auto-tagger and Once Sport Analyser exporter.

**Collect in-house** (no Grokbot): film → smart watch → 5-min re-pass → Review → Home/Away XML.

## Upload a match (one path)

1. Start the stack: `./run_local.sh` (FastAPI **must** be on `:8000` — that is the uploader)
2. Open the dashboard → **Upload match film** (4 MB chunks, auto-retries)
3. When it says Saved → set kit colours + line-up → **Analyse Stats**
4. Review tags → download Home.xml / Away.xml

There is no Streamlit film picker, no link paste, and no second uploader.
Analyse always uses the newest film in the inbox.

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
# or separately:
#   uvicorn api.main:app --host 127.0.0.1 --port 8000
#   streamlit run app/dashboard.py
```

## Monorepo

| Path | Role |
|------|------|
| `api/film_upload.py` | **Only** match-film upload (chunked) |
| `analytics/lineups.py` | CSV/JSON line-up import |
| `analytics/jersey_ocr.py` | Shirt-number OCR (Tesseract) |
| `analytics/batch_collect.py` | Queue all inbox films |
| `analytics/smart_detect.py` | YOLO / HOG people finder |
| `analytics/block_coverage.py` | 5-minute coverage + re-pass |
| `analytics/review_edits.py` | Review fixes before export |
| `analytics/oncesport_export.py` | Home/Away OnceSport XML |
| `analytics/video_auto_collect.py` | Film → events → XML |
