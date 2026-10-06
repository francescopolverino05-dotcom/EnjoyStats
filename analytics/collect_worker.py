"""Background worker that claims queued Analyse jobs and runs them.

On Railway: run this as a separate **Worker** service so the web site stays
responsive while full-match CV burns CPU for hours.

  Web:    enqueue only (STATMAN_USE_EXTERNAL_WORKER=1)
  Worker: python -m analytics.collect_worker

Both services must share the same jobs + inbox paths (Railway volume), e.g.

  ENJOYSTATS_JOBS_DIR=/data/jobs
  ENJOYSTATS_FILM_INBOX=/data/inbox
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from analytics.collect_job import (
    JOB_SUFFIX,
    _now,
    _write_json,
    collect_jobs_dir,
    read_job_status,
    run_collect_job,
)


def worker_poll_seconds() -> float:
    raw = os.environ.get("STATMAN_WORKER_POLL_S", "").strip()
    try:
        value = float(raw) if raw else 3.0
    except ValueError:
        value = 3.0
    return max(0.5, value)


def use_external_worker() -> bool:
    """True when the web process should only enqueue, not spawn collect."""

    flag = os.environ.get("STATMAN_USE_EXTERNAL_WORKER", "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def list_queued_job_paths(folder: Path | None = None) -> list[Path]:
    """Oldest queued status files first."""

    root = folder or collect_jobs_dir()
    if not root.is_dir():
        return []
    queued: list[tuple[float, Path]] = []
    for path in root.glob(f"*{JOB_SUFFIX}"):
        status = read_job_status(path)
        if status is None:
            continue
        if str(status.get("state") or "").strip().lower() != "queued":
            continue
        queued.append((path.stat().st_mtime, path))
    queued.sort(key=lambda item: item[0])
    return [path for _mtime, path in queued]


def claim_job(status_path: Path) -> dict[str, Any] | None:
    """Atomically claim a queued job. Returns status payload or ``None``."""

    lock_path = status_path.with_suffix(status_path.suffix + ".claim")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    try:
        os.write(fd, f"pid={os.getpid()}\n".encode("utf-8"))
    finally:
        os.close(fd)

    status = read_job_status(status_path)
    if status is None or str(status.get("state") or "").strip().lower() != "queued":
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    status["state"] = "claimed"
    status["label"] = "Worker claimed job…"
    status["pid"] = os.getpid()
    status["updated_at"] = _now()
    _write_json(status_path, status)
    return status


def release_claim(status_path: Path) -> None:
    lock_path = status_path.with_suffix(status_path.suffix + ".claim")
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def process_one(status_path: Path) -> bool:
    """Claim and run one job. Returns True if a job was processed."""

    status = claim_job(status_path)
    if status is None:
        return False
    film_raw = str(status.get("film") or "").strip()
    if not film_raw:
        status["state"] = "error"
        status["error"] = "Queued job has no film path."
        status["label"] = "Collect failed (no film path)"
        status["updated_at"] = _now()
        _write_json(status_path, status)
        release_claim(status_path)
        return True

    film = Path(film_raw)
    sys.stdout.write(f"[statman-worker] starting {status_path.name} · {film.name}\n")
    sys.stdout.flush()
    try:
        run_collect_job(film, status_path)
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"[statman-worker] failed {status_path.name}: {exc}\n")
        sys.stderr.flush()
    finally:
        release_claim(status_path)
    return True


def poll_once() -> bool:
    """Process the oldest queued job if any. Returns True when work ran."""

    paths = list_queued_job_paths()
    for path in paths:
        if process_one(path):
            return True
    return False


def run_forever(*, poll_s: float | None = None) -> None:
    """Block forever, claiming queued Analyse jobs."""

    interval = worker_poll_seconds() if poll_s is None else max(0.5, float(poll_s))
    jobs = collect_jobs_dir()
    try:
        jobs.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(
            f"[statman-worker] cannot create jobs dir {jobs}: {exc}\n"
            "Mount a Railway volume at /data and set ENJOYSTATS_JOBS_DIR=/data/jobs\n"
        )
        sys.stderr.flush()
        raise SystemExit(1) from exc
    sys.stdout.write(
        f"[statman-worker] watching {jobs} every {interval:.1f}s "
        f"(external_worker={use_external_worker()})\n"
    )
    sys.stdout.flush()
    while True:
        try:
            worked = poll_once()
        except Exception as exc:  # noqa: BLE001 — keep worker alive
            sys.stderr.write(f"[statman-worker] poll error: {exc}\n")
            sys.stderr.flush()
            worked = False
        if not worked:
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m analytics.collect_worker`` (or ``--once``)."""

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        sys.stdout.write(
            "Usage: python -m analytics.collect_worker [--once]\n"
            "  Poll ENJOYSTATS_JOBS_DIR for queued Analyse jobs and run them.\n"
        )
        return 0
    try:
        if args and args[0] == "--once":
            poll_once()
            return 0
        run_forever()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — show boot errors clearly on Railway
        sys.stderr.write(f"[statman-worker] fatal: {exc}\n")
        sys.stderr.flush()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
