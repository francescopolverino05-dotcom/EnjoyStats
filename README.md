# STAT MAN

Football match auto-tagger and Once Sport Analyser exporter.

Upload (or link) a match film → STAT MAN tags the full match in **5-minute blocks** → review/edit → export **Home** and **Away** Once Sport Analyser XMLs → team/player stats dashboard.

## Monorepo

| Path | Role |
|------|------|
| `apps/web` | Next.js dark UI (setup, progress, review, export, stats) |
| `packages/core` | Types, stats engine, xG estimate, OnceSport XML codec |
| `packages/db` | Drizzle schema (SQLite locally, Postgres-ready) |
| `services/tagger` | Block tagging worker (coverage + re-pass) |

Legacy Python EnjoyStats code remains at the repo root during migration (`analytics/`, `app/`, `api/`). New work lives under `apps/` and `packages/`.

## Quick start

```bash
pnpm install
pnpm --filter @statman/core test
pnpm dev
```

Open http://localhost:3000

## Environment

Copy `.env.example` values into `apps/web/.env.local`:

- `DATABASE_URL` — SQLite file or Postgres DSN
- `STATMAN_BOT_URL` — optional Grok Bot StatMan link
- `STATMAN_WEBHOOK_URL` / `STATMAN_WEBHOOK_KEY` — optional routine webhook

## Tag names

Configurable Once Sport button labels live in `packages/core/src/tags/default-tags.json`.
Supply your Exact button list and we plug it in without renaming.
