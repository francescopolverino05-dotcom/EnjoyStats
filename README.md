# STAT MAN / EnjoyStats

Football match auto-tagger and Once Sport Analyser exporter.

**Collect in-house** (no Grokbot): film → smart watch → 5-min re-pass → Review → Home/Away XML.

### How collect works (simple)
1. **Watch** the film (YOLO person finder when installed, else HOG + pitch blobs; kit colours)
2. Split into **5-minute chunks**; thin chunks (< 30 tags) are watched again (up to 2 denser rounds)
3. Open **Review tags** — fix only wrong rows
4. Download Home.xml / Away.xml with your exact OnceSport buttons

### Accuracy (honest)
Broadcast film alone cannot be 100% perfect. The path to board-ready is:
**dense computer watch + re-pass + human Review of mistakes**.  
Review is how you push accuracy to the top before public release.

## Monorepo

| Path | Role |
|------|------|
| `apps/web` | Next.js dark UI (setup, progress, review, export, stats) |
| `packages/core` | Types, stats engine, xG estimate, OnceSport XML codec |
| `packages/db` | Drizzle schema (SQLite locally, Postgres-ready) |
| `services/tagger` | Block tagging worker (coverage + re-pass) |
| `analytics/smart_detect.py` | YOLO / HOG people finder |
| `analytics/block_coverage.py` | 5-minute coverage + re-pass flags |
| `analytics/review_edits.py` | Apply Review fixes before export |
| `analytics/oncesport_export.py` | Home/Away OnceSport XML |
| `analytics/video_auto_collect.py` | Full-match film → events → XML |

## Quick start (EnjoyStats collect)

```bash
pip install -r requirements.txt   # includes ultralytics (YOLO)
streamlit run app/dashboard.py
```

1. Set Home / Away kit colours
2. Upload match MP4 → **Analyse Stats**
3. Open match → **Review tags** → fix wrong rows → Apply
4. Download Home / Away OnceSport XMLs

## Environment

Optional:

- `STATMAN_BOT_URL` — external import only (not required)
- `DATABASE_URL` — SQLite / Postgres for the TS app
