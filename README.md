# STAT MAN / EnjoyStats

Football match auto-tagger and Once Sport Analyser exporter.

**Collect in-house** (no Grokbot): film → smart watch → 5-min re-pass → Review → Home/Away XML.

## Tomorrow night — game week batch (simple)

1. Drop every match MP4 into the **inbox** folder  
2. Upload the **line-up CSV** (names + shirt numbers)  
3. Set Home / Away **kit colours**  
4. Click **Collect ALL inbox films**  
5. When a match finishes → **Review tags** (fix only wrong rows) → download Home.xml / Away.xml  

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
streamlit run app/dashboard.py
```

## Monorepo

| Path | Role |
|------|------|
| `analytics/lineups.py` | CSV/JSON line-up import |
| `analytics/jersey_ocr.py` | Shirt-number OCR (Tesseract) |
| `analytics/batch_collect.py` | Queue all inbox films |
| `analytics/smart_detect.py` | YOLO / HOG people finder |
| `analytics/block_coverage.py` | 5-minute coverage + re-pass |
| `analytics/review_edits.py` | Review fixes before export |
| `analytics/oncesport_export.py` | Home/Away OnceSport XML |
| `analytics/video_auto_collect.py` | Film → events → XML |
