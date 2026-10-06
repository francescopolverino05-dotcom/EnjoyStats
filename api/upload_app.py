"""Slim FastAPI app for film upload only (no Postgres).

Used on Railway Web and any host where the full ``api.main`` stack is too
heavy. The portal / Streamlit supervisor can point Uvicorn here via
``ENJOYSTATS_UVICORN_APP=api.upload_app:app`` or ``STATMAN_UPLOAD_ONLY=1``.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.film_upload import film_router

app = FastAPI(
    title="StatMan film upload",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get("/health")
def health() -> dict[str, bool]:
    """Liveness for portal / Railway health checks."""

    return {"ok": True}


app.include_router(film_router)
