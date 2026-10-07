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
from datetime import datetime, timezone
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

STALE_CLAIM_S = 120.0


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


def _parse_iso(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _age_seconds(status: dict[str, Any]) -> float:
    stamp = _parse_iso(str(status.get("updated_at") or status.get("started_at") or ""))
    if stamp is None:
        return 0.0
    return max(0.0, time.time() - stamp)


def reclaim_stale_jobs(folder: Path | None = None) -> int:
    """Clear orphaned claim locks and re-queue dead claimed/running jobs."""

    root = folder or collect_jobs_dir()
    if not root.is_dir():
        return 0
    fixed = 0
    for path in root.glob(f"*{JOB_SUFFIX}"):
        status = read_job_status(path)
        if status is None:
            continue
        state = str(status.get("state") or "").strip().lower()
        lock_path = path.with_suffix(path.suffix + ".claim")
        age = _age_seconds(status)
        pid = int(status.get("pid") or 0)

        # Queued forever because a crashed worker left the .claim lock behind.
        if state == "queued" and lock_path.is_file():
            lock_age = time.time() - lock_path.stat().st_mtime
            if lock_age >= STALE_CLAIM_S or not _pid_alive(pid):
                try:
                    lock_path.unlink(missing_ok=True)
                    fixed += 1
                    sys.stdout.write(
                        f"[statman-worker] cleared stale claim lock {lock_path.name}\n"
                    )
                    sys.stdout.flush()
                except OSError:
                    pass

        # Claimed/running with a dead worker — put back on the queue.
        # Long-running collects are fine while the worker pid is still alive.
        if state in {"claimed", "running"} and age >= STALE_CLAIM_S and not _pid_alive(pid):
            status["state"] = "queued"
            status["label"] = "Re-queued after worker restart…"
            status["pid"] = 0
            status["error"] = ""
            status["updated_at"] = _now()
            _write_json(path, status)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            fixed += 1
            sys.stdout.write(f"[statman-worker] re-queued stale {path.name} (was {state})\n")
            sys.stdout.flush()
    return fixed


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
        lock_path = path.with_suffix(path.suffix + ".claim")
        if lock_path.is_file():
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
    if not film.is_file():
        status["state"] = "error"
        status["error"] = (
            f"Film not found at {film}. "
            "Web and Worker must share the same /data volume "
            "(inbox + jobs)."
        )
        status["label"] = "Collect failed (film missing on worker)"
        status["updated_at"] = _now()
        _write_json(status_path, status)
        release_claim(status_path)
        sys.stderr.write(f"[statman-worker] {status['error']}\n")
        sys.stderr.flush()
        return True

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

    reclaim_stale_jobs()
    paths = list_queued_job_paths()
    for path in paths:
        if process_one(path):
            return True
    return False


def ensure_jobs_dir() -> Path:
    """Create the shared jobs directory. Never silently switch off /data."""

    primary = collect_jobs_dir()
    try:
        primary.mkdir(parents=True, exist_ok=True)
        probe = primary / ".statman_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return primary
    except OSError as exc:
        # Falling back to /tmp would desync Web (writes /data) from Worker.
        sys.stderr.write(
            f"[statman-worker] FATAL: cannot write {primary} ({exc}). "
            "Attach the SAME Railway volume at /data on Web and Worker, "
            "then redeploy Worker.\n"
        )
        sys.stderr.flush()
        raise


def _describe_jobs_dir(jobs: Path) -> str:
    """One-line inventory for Railway logs (shared-volume debugging)."""

    heartbeat = jobs / ".web_enqueue_heartbeat"
    hb = "yes" if heartbeat.is_file() else "NO — Web may be on a different volume"
    statuses = sorted(jobs.glob(f"*{JOB_SUFFIX}"))
    states: list[str] = []
    for path in statuses[:12]:
        doc = read_job_status(path)
        state = str((doc or {}).get("state") or "?")
        states.append(f"{path.name}:{state}")
    extra = "" if len(statuses) <= 12 else f" (+{len(statuses) - 12} more)"
    listing = ", ".join(states) if states else "(none)"
    return f"web_heartbeat={hb} · files={len(statuses)} [{listing}]{extra}"


def run_forever(*, poll_s: float | None = None) -> None:
    """Block forever, claiming queued Analyse jobs."""

    interval = worker_poll_seconds() if poll_s is None else max(0.5, float(poll_s))
    jobs = ensure_jobs_dir()
    queued_n = len(list_queued_job_paths(jobs))
    sys.stdout.write(
        f"[statman-worker] watching {jobs} every {interval:.1f}s "
        f"(queued={queued_n}, utc={datetime.now(timezone.utc).isoformat()})\n"
    )
    sys.stdout.write(f"[statman-worker] {_describe_jobs_dir(jobs)}\n")
    sys.stdout.flush()
    idle_loops = 0
    while True:
        try:
            worked = poll_once()
        except Exception as exc:  # noqa: BLE001 — keep worker alive
            sys.stderr.write(f"[statman-worker] poll error: {exc}\n")
            sys.stderr.flush()
            worked = False
        if worked:
            idle_loops = 0
        else:
            idle_loops += 1
            if idle_loops % 20 == 0:
                n = len(list_queued_job_paths(jobs))
                sys.stdout.write(f"[statman-worker] idle · watching {jobs} · queued={n}\n")
                sys.stdout.write(f"[statman-worker] {_describe_jobs_dir(jobs)}\n")
                sys.stdout.flush()
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
