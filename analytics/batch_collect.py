"""Batch-collect every film in the inbox (game-week workflow)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from analytics.collect_job import (
    _now,
    _write_json,
    collect_jobs_dir,
    start_collect_job,
)
from analytics.video_auto_collect import VIDEO_SUFFIXES, film_inbox_dir, list_ready_films


def list_inbox_films() -> list[Path]:
    """Video files waiting in the inbox (not XML)."""

    inbox = film_inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    films = [path for path in list_ready_films(inbox) if path.suffix.lower() in VIDEO_SUFFIXES]
    return sorted(films, key=lambda path: path.name.lower())


def start_batch_collect(
    films: list[Path] | None = None,
    *,
    home_kit_hex: str | None = None,
    away_kit_hex: str | None = None,
    home_team_name: str | None = None,
    away_team_name: str | None = None,
    lineup_json: str | None = None,
) -> Path:
    """Queue one background job per film. Returns the batch status path."""

    selected = films if films is not None else list_inbox_films()
    if not selected:
        raise ValueError("No films in the inbox. Drop match MP4s into the inbox folder first.")
    folder = collect_jobs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    batch_id = uuid4().hex[:12]
    batch_path = folder / f"batch_{batch_id}.json"
    job_paths: list[str] = []
    for film in selected:
        status = start_collect_job(
            film,
            home_kit_hex=home_kit_hex,
            away_kit_hex=away_kit_hex,
            home_team_name=home_team_name,
            away_team_name=away_team_name,
            lineup_json=lineup_json,
        )
        job_paths.append(str(status))
    payload: dict[str, Any] = {
        "batch_id": batch_id,
        "state": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "film_count": len(selected),
        "films": [str(path) for path in selected],
        "jobs": job_paths,
        "home_team_name": home_team_name or "",
        "away_team_name": away_team_name or "",
    }
    _write_json(batch_path, payload)
    return batch_path


def read_batch_status(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def summarize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    """Count done / running / error jobs for a batch."""

    from analytics.collect_job import read_job_status

    done = running = error = queued = 0
    labels: list[str] = []
    for raw in batch.get("jobs") or []:
        status = read_job_status(Path(str(raw)))
        if status is None:
            queued += 1
            continue
        state = str(status.get("state") or "queued")
        if state == "done":
            done += 1
        elif state == "error":
            error += 1
        elif state == "running":
            running += 1
        else:
            queued += 1
        labels.append(f"{Path(str(status.get('film') or raw)).name}: {state}")
    total = max(int(batch.get("film_count") or 0), len(batch.get("jobs") or []))
    if done == total and total > 0 and error == 0:
        state = "done"
    elif error and done + error == total:
        state = "done_with_errors"
    elif running or done:
        state = "running"
    else:
        state = "queued"
    return {
        "state": state,
        "done": done,
        "running": running,
        "error": error,
        "queued": queued,
        "total": total,
        "labels": labels,
    }
