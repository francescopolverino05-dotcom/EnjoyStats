"""Background full-match collect — upload, walk away, read the sheet when ready.

Impact Soccer processes a match in hours and emails when it is done. EnjoyStats
does the same locally: a subprocess watches the film, writes progress to a
status file, and dumps the rundown JSON + sidecar XML when it finishes.
The Streamlit tab does not have to stay blocked on the request.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from analytics.game_ingest import MatchRundown, rundown_from_mapping, rundown_to_json

JOB_SUFFIX = ".status.json"
RUNDOWN_SUFFIX = ".rundown.json"


def collect_jobs_dir() -> Path:
    """Directory for job status files (gitignored under ``.local-run``)."""

    override = os.environ.get("ENJOYSTATS_JOBS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[1] / ".local-run" / "jobs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_job_status(path: Path) -> dict[str, Any] | None:
    """Load a job status document, or ``None`` if it is missing."""

    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def latest_job_status_path() -> Path | None:
    """Newest job status file, so a later refresh can resume the same analyse."""

    folder = collect_jobs_dir()
    if not folder.is_dir():
        return None
    files = sorted(
        folder.glob(f"*{JOB_SUFFIX}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def latest_collect_job() -> dict[str, Any] | None:
    """Newest job status in the jobs directory."""

    path = latest_job_status_path()
    if path is None:
        return None
    return read_job_status(path)


def load_job_rundown(status: dict[str, Any]) -> MatchRundown | None:
    """Rehydrate the rundown a finished job wrote."""

    raw = str(status.get("rundown_path", "")).strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return rundown_from_mapping(payload)


def run_collect_job(film: Path, status_path: Path) -> MatchRundown:
    """Collect a film in-process, updating ``status_path`` as it watches."""

    from app.ingest import collect_from_film_path

    status: dict[str, Any] = {
        "job_id": status_path.stem.replace(".status", "") if status_path.stem else uuid4().hex[:12],
        "state": "running",
        "film": str(film),
        "label": "Opening match film…",
        "fraction": 0.02,
        "pid": os.getpid(),
        "error": "",
        "rundown_path": "",
        "started_at": _now(),
        "updated_at": _now(),
    }
    _write_json(status_path, status)

    def _progress(label: str, fraction: float) -> None:
        status["label"] = label
        status["fraction"] = float(fraction)
        status["state"] = "running"
        status["updated_at"] = _now()
        _write_json(status_path, status)

    try:
        rundown = collect_from_film_path(film, on_progress=_progress)
    except (ValueError, OSError) as exc:
        status["state"] = "error"
        status["error"] = str(exc)
        status["label"] = f"Collect failed ({exc})"
        status["updated_at"] = _now()
        _write_json(status_path, status)
        raise

    rundown_path = status_path.with_name(status_path.name.replace(JOB_SUFFIX, RUNDOWN_SUFFIX))
    if rundown_path == status_path:
        rundown_path = status_path.with_suffix(".rundown.json")
    _write_json(rundown_path, rundown_to_json(rundown))
    status["state"] = "done"
    status["fraction"] = 1.0
    status["label"] = (
        f"Ready · {rundown.summary.event_count} events · "
        f"{rundown.summary.goals} goals · {rundown.summary.passes} passes"
    )
    status["rundown_path"] = str(rundown_path)
    status["updated_at"] = _now()
    _write_json(status_path, status)
    return rundown


def start_collect_job(film: Path) -> Path:
    """Spawn a detached process that collects ``film``. Returns the status path."""

    resolved = film.expanduser().resolve()
    folder = collect_jobs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    job_id = uuid4().hex[:12]
    status_path = folder / f"{job_id}{JOB_SUFFIX}"
    log_path = folder / f"{job_id}.log"
    _write_json(
        status_path,
        {
            "job_id": job_id,
            "state": "queued",
            "film": str(resolved),
            "label": "Queued full-match collect…",
            "fraction": 0.0,
            "pid": 0,
            "error": "",
            "rundown_path": "",
            "started_at": _now(),
            "updated_at": _now(),
        },
    )
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(root) + ((os.pathsep + existing) if existing else "")
    log_handle = log_path.open("w", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "-m", "analytics.collect_job", str(resolved), str(status_path)],
        cwd=str(root),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return status_path


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m analytics.collect_job FILM STATUS.json``."""

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        sys.stderr.write("Usage: python -m analytics.collect_job FILM STATUS.json\n")
        return 2
    film = Path(args[0])
    status_path = Path(args[1])
    try:
        run_collect_job(film, status_path)
    except (ValueError, OSError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
