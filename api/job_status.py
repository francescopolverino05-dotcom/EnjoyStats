"""Read-only Analyse job status for Railway debugging."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from analytics.collect_job import JOB_SUFFIX, collect_jobs_dir, read_job_status
from analytics.collect_worker import (
    MAX_FILM_CRASHES,
    film_crash_count,
    job_age_seconds,
    read_film_crash_ledger,
)

job_status_router = APIRouter(tags=["jobs"])


def _public_job(path: Path, status: dict[str, Any]) -> dict[str, Any]:
    film = str(status.get("film") or "")
    crashes = film_crash_count(film) if film else 0
    return {
        "file": path.name,
        "state": status.get("state"),
        "fraction": status.get("fraction"),
        "label": status.get("label"),
        "updated_at": status.get("updated_at"),
        "started_at": status.get("started_at"),
        "age_seconds": int(job_age_seconds(status)),
        "film": Path(film).name if film else "",
        "error": status.get("error") or "",
        "stall_restarts": status.get("stall_restarts") or 0,
        "film_crashes": crashes,
        "film_crash_limit": MAX_FILM_CRASHES,
        "pid": status.get("pid") or 0,
    }


@job_status_router.get("/api/v1/jobs")
def list_jobs() -> dict[str, Any]:
    """List Analyse status files under ENJOYSTATS_JOBS_DIR (newest first)."""

    root = collect_jobs_dir()
    jobs: list[dict[str, Any]] = []
    ledgers: list[dict[str, Any]] = []
    if root.is_dir():
        paths = sorted(
            root.glob(f"*{JOB_SUFFIX}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        seen_films: set[str] = set()
        for path in paths[:40]:
            status = read_job_status(path)
            if status is None:
                continue
            jobs.append(_public_job(path, status))
            film = str(status.get("film") or "").strip()
            if film and film not in seen_films:
                seen_films.add(film)
                ledgers.append(read_film_crash_ledger(film))
    return {
        "jobs_dir": str(root),
        "count": len(jobs),
        "jobs": jobs,
        "crash_ledgers": ledgers,
        "max_film_crashes": MAX_FILM_CRASHES,
    }
