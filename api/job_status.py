"""Read-only Analyse job status for Railway debugging."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from analytics.collect_job import JOB_SUFFIX, collect_jobs_dir, read_job_status
from analytics.collect_worker import job_age_seconds

job_status_router = APIRouter(tags=["jobs"])


def _public_job(path: Path, status: dict[str, Any]) -> dict[str, Any]:
    film = str(status.get("film") or "")
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
        "pid": status.get("pid") or 0,
    }


@job_status_router.get("/api/v1/jobs")
def list_jobs() -> dict[str, Any]:
    """List Analyse status files under ENJOYSTATS_JOBS_DIR (newest first)."""

    root = collect_jobs_dir()
    jobs: list[dict[str, Any]] = []
    if root.is_dir():
        paths = sorted(
            root.glob(f"*{JOB_SUFFIX}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in paths[:40]:
            status = read_job_status(path)
            if status is None:
                continue
            jobs.append(_public_job(path, status))
    return {"jobs_dir": str(root), "count": len(jobs), "jobs": jobs}
