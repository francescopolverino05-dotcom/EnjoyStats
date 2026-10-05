# STAT MAN / EnjoyStats

Football match auto-tagger and Once Sport Analyser exporter.

**Collect in-house** (no Grokbot required): upload a match film → Analyse Stats →
download **Home** and **Away** OnceSport XMLs using your exact button labels
(Passaggi, Duelli aeree, …).

## Monorepo

| Path | Role |
|------|------|
| `apps/web` | Next.js dark UI (setup, progress, review, export, stats) |
| `packages/core` | Types, stats engine, xG estimate, OnceSport XML codec |
| `packages/db` | Drizzle schema (SQLite locally, Postgres-ready) |
| `services/tagger` | Block tagging worker (coverage + re-pass) |
| `analytics/oncesport_export.py` | Independent Home/Away OnceSport XML from collected events |
| `analytics/video_auto_collect.py` | Full-match film CV → events → rundown + OnceSport XMLs |

Legacy Python EnjoyStats (`app/dashboard.py`) remains the live Analyse UI during migration.

## Quick start (EnjoyStats collect)

```bash
streamlit run app/dashboard.py
```

1. Drop or upload a match MP4
2. Click **Analyse Stats**
3. Download `{team}_Home.xml` / `{team}_Away.xml` — labels match your OnceSport panel exactly

## Quick start (TypeScript UI)

```bash
pnpm install
pnpm --filter @statman/core test
pnpm dev
```

Open http://localhost:3000

## Tag names

Exact Once Sport button labels live in:
- `packages/core/src/tags/default-tags.json` (TypeScript)
- `analytics/oncesport_export.py` (Python film → XML)

Never renamed on export.

## Environment

Optional (not required for collect):

- `STATMAN_BOT_URL` — external Grok Bot link (import-only)
- `STATMAN_WEBHOOK_URL` / `STATMAN_WEBHOOK_KEY` — optional external webhook
- `DATABASE_URL` — SQLite file or Postgres DSN
