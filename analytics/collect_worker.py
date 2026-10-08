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

import multiprocessing as mp
import os
import signal
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
# No progress file update for this long ⇒ treat as hung (even if pid still alive).
DEFAULT_STALE_PROGRESS_S = 900.0
MAX_STALL_RESTARTS = 2
# Hard stop after this many crashes for the same film (across job files).
MAX_FILM_CRASHES = 3
OOM_FIX_MARKER = ".oom_fix_v1_cleared_crashes"


def collect_in_process() -> bool:
    """Prefer in-process collect on the embed Web dyno (fork+YOLO OOMs Railway)."""

    flag = os.environ.get("STATMAN_COLLECT_INPROCESS", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    emb = os.environ.get("STATMAN_EMBED_WORKER", "").strip().lower()
    return emb in {"1", "true", "yes", "on"}


def worker_poll_seconds() -> float:
    raw = os.environ.get("STATMAN_WORKER_POLL_S", "").strip()
    try:
        value = float(raw) if raw else 3.0
    except ValueError:
        value = 3.0
    return max(0.5, value)


def stale_progress_seconds() -> float:
    raw = os.environ.get("STATMAN_STALE_PROGRESS_S", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_STALE_PROGRESS_S
    except ValueError:
        value = DEFAULT_STALE_PROGRESS_S
    return max(120.0, value)


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


def job_age_seconds(status: dict[str, Any]) -> float:
    """Seconds since status ``updated_at`` (or ``started_at``)."""

    stamp = _parse_iso(str(status.get("updated_at") or status.get("started_at") or ""))
    if stamp is None:
        return 0.0
    return max(0.0, time.time() - stamp)


# Back-compat alias for older imports/tests.
_age_seconds = job_age_seconds


def _kill_pid(pid: int) -> None:
    """Best-effort terminate a hung collect child (never signal ourselves)."""

    if pid <= 1 or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _requeue_status(
    path: Path,
    status: dict[str, Any],
    *,
    reason: str,
    kill_pid: bool = False,
    count_stall: bool = False,
) -> None:
    pid = int(status.get("pid") or 0)
    if kill_pid:
        _kill_pid(pid)
    stalls = int(status.get("stall_restarts") or 0)
    if count_stall:
        stalls += 1
    lock_path = path.with_suffix(path.suffix + ".claim")
    if count_stall and stalls > MAX_STALL_RESTARTS:
        status["state"] = "error"
        status["error"] = (
            f"Analyse stalled {stalls} times with no progress update "
            f"({reason}). Redeploy Web or re-upload the film."
        )
        status["label"] = "Collect failed (stalled)"
        status["pid"] = 0
        status["stall_restarts"] = stalls
        status["updated_at"] = _now()
        _write_json(path, status)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        sys.stdout.write(f"[statman-worker] gave up on {path.name} after {stalls} stalls\n")
        sys.stdout.flush()
        return

    status["state"] = "queued"
    status["label"] = f"Re-queued ({reason})…"
    status["pid"] = 0
    status["error"] = ""
    status["stall_restarts"] = stalls
    status["updated_at"] = _now()
    _write_json(path, status)
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass
    sys.stdout.write(
        f"[statman-worker] re-queued {path.name} ({reason}, stall={stalls})\n"
    )
    sys.stdout.flush()


def reclaim_stale_jobs(folder: Path | None = None) -> int:
    """Clear orphaned claim locks and re-queue dead/hung claimed/running jobs."""

    root = folder or collect_jobs_dir()
    if not root.is_dir():
        return 0
    fixed = 0
    progress_limit = stale_progress_seconds()
    for path in root.glob(f"*{JOB_SUFFIX}"):
        status = read_job_status(path)
        if status is None:
            continue
        state = str(status.get("state") or "").strip().lower()
        lock_path = path.with_suffix(path.suffix + ".claim")
        age = job_age_seconds(status)
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

        if state not in {"claimed", "running"}:
            continue

        # Dead worker process — put back on the queue.
        if age >= STALE_CLAIM_S and not _pid_alive(pid):
            _requeue_status(path, status, reason="worker dead")
            fixed += 1
            continue

        # Hung collect: pid may still be alive but progress file is frozen
        # (classic “stuck at 29% overnight”).
        if age >= progress_limit:
            _requeue_status(
                path,
                status,
                reason=f"no progress for {int(age)}s",
                kill_pid=True,
                count_stall=True,
            )
            fixed += 1
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


def _collect_child(film: str, status_path: str) -> None:
    """Subprocess entry: run collect and exit (parent watches progress)."""

    import traceback

    try:
        run_collect_job(Path(film), Path(status_path))
    except BaseException as exc:  # noqa: BLE001 — persist real crash reason for UI
        detail = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(f"[statman-worker] child failed: {detail}\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        path = Path(status_path)
        doc = read_job_status(path) or {}
        if str(doc.get("state") or "").strip().lower() not in {"done", "error"}:
            doc["state"] = "error"
            doc["error"] = detail[:800]
            doc["label"] = f"Collect failed ({type(exc).__name__})"
            doc["updated_at"] = _now()
            try:
                _write_json(path, doc)
            except OSError:
                pass
        raise SystemExit(1) from exc


def _film_key(film: str) -> str:
    try:
        return str(Path(film).resolve())
    except OSError:
        return str(Path(film))


def _film_crash_ledger_path(film: str, folder: Path | None = None) -> Path:
    root = folder or collect_jobs_dir()
    safe = Path(film).name.replace(" ", "_")
    return root / f".crashes.{safe}.json"


def read_film_crash_ledger(film: str, folder: Path | None = None) -> dict[str, Any]:
    path = _film_crash_ledger_path(film, folder)
    if not path.is_file():
        return {"film": Path(film).name, "count": 0, "events": []}
    try:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"film": Path(film).name, "count": 0, "events": []}
    if not isinstance(raw, dict):
        return {"film": Path(film).name, "count": 0, "events": []}
    return raw


def film_crash_count(film: str, folder: Path | None = None) -> int:
    try:
        return max(0, int(read_film_crash_ledger(film, folder).get("count") or 0))
    except (TypeError, ValueError):
        return 0


def record_film_crash(
    film: str,
    *,
    detail: str,
    fraction: float | None = None,
    folder: Path | None = None,
) -> int:
    """Increment per-film crash count. Returns the new count."""

    root = folder or collect_jobs_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = _film_crash_ledger_path(film, root)
    doc = read_film_crash_ledger(film, root)
    events = list(doc.get("events") or [])
    events.append(
        {
            "at": _now(),
            "detail": str(detail)[:500],
            "fraction": fraction,
        }
    )
    count = len(events)
    payload = {
        "film": Path(film).name,
        "film_key": _film_key(film),
        "count": count,
        "stopped": count >= MAX_FILM_CRASHES,
        "events": events[-20:],
        "updated_at": _now(),
    }
    _write_json(path, payload)
    sys.stdout.write(
        f"[statman-worker] film crash {count}/{MAX_FILM_CRASHES} · {Path(film).name} · {detail[:120]}\n"
    )
    sys.stdout.flush()
    return count


def stop_film_jobs(
    film: str,
    *,
    reason: str,
    folder: Path | None = None,
) -> int:
    """Mark queued/claimed/running jobs for ``film`` as stopped. Returns count."""

    root = folder or collect_jobs_dir()
    if not root.is_dir():
        return 0
    key = _film_key(film)
    stopped = 0
    for path in root.glob(f"*{JOB_SUFFIX}"):
        status = read_job_status(path)
        if status is None:
            continue
        other = str(status.get("film") or "").strip()
        if not other or _film_key(other) != key:
            continue
        state = str(status.get("state") or "").strip().lower()
        if state not in {"queued", "claimed", "running"}:
            continue
        pid = int(status.get("pid") or 0)
        _kill_pid(pid)
        status["state"] = "error"
        status["error"] = reason
        status["label"] = "Stopped after 3 crashes"
        status["pid"] = 0
        status["updated_at"] = _now()
        _write_json(path, status)
        release_claim(path)
        stopped += 1
        sys.stdout.write(f"[statman-worker] stopped {path.name}: {reason}\n")
        sys.stdout.flush()
    return stopped


def _active_films(folder: Path | None = None) -> set[str]:
    """Films already claimed/running — do not start a second collect on them."""

    root = folder or collect_jobs_dir()
    active: set[str] = set()
    if not root.is_dir():
        return active
    for path in root.glob(f"*{JOB_SUFFIX}"):
        status = read_job_status(path)
        if status is None:
            continue
        state = str(status.get("state") or "").strip().lower()
        if state not in {"claimed", "running"}:
            continue
        film = str(status.get("film") or "").strip()
        if film:
            active.add(_film_key(film))
    return active


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

    crashes = film_crash_count(film_raw)
    if crashes >= MAX_FILM_CRASHES:
        status["state"] = "error"
        status["error"] = (
            f"Stopped: this film already crashed {crashes} times. "
            "Fix the worker, then delete "
            f"`{_film_crash_ledger_path(film_raw).name}` under jobs to retry."
        )
        status["label"] = "Stopped after 3 crashes"
        status["updated_at"] = _now()
        _write_json(status_path, status)
        release_claim(status_path)
        return True

    # Another job is already watching this film (duplicates from re-queue storms).
    for path in collect_jobs_dir().glob(f"*{JOB_SUFFIX}"):
        if path.resolve() == status_path.resolve():
            continue
        doc = read_job_status(path)
        if doc is None:
            continue
        if str(doc.get("state") or "").strip().lower() not in {"claimed", "running"}:
            continue
        other_film = str(doc.get("film") or "").strip()
        if other_film and _film_key(other_film) == _film_key(film_raw):
            status["state"] = "error"
            status["error"] = (
                f"Skipped duplicate — already analysing {film.name} "
                f"via {path.name}."
            )
            status["label"] = "Skipped (duplicate job)"
            status["updated_at"] = _now()
            _write_json(status_path, status)
            release_claim(status_path)
            sys.stdout.write(f"[statman-worker] skip duplicate {status_path.name}\n")
            sys.stdout.flush()
            return True

    sys.stdout.write(f"[statman-worker] starting {status_path.name} · {film.name}\n")
    sys.stdout.flush()

    # Embed Web dyno: run collect in-process. Fork + YOLO doubled RAM and
    # OOM-killed the whole Railway container (site 502).
    if collect_in_process():
        sys.stdout.write(
            f"[statman-worker] in-process collect "
            f"(hz={os.environ.get('STATMAN_SAMPLE_HZ', '?')} "
            f"side={os.environ.get('STATMAN_MAX_SIDE', '?')} "
            f"yolo_off={os.environ.get('STATMAN_DISABLE_YOLO', '0')})\n"
        )
        sys.stdout.flush()
        live = read_job_status(status_path) or status
        live["pid"] = os.getpid()
        live["updated_at"] = _now()
        _write_json(status_path, live)
        try:
            run_collect_job(film, status_path)
        except Exception as exc:  # noqa: BLE001 — count toward film crash budget
            doc = read_job_status(status_path) or live
            detail = f"{type(exc).__name__}: {exc}"
            if not str(doc.get("error") or "").strip():
                doc["error"] = detail[:800]
                doc["state"] = "error"
                doc["label"] = f"Collect failed ({type(exc).__name__})"
                doc["updated_at"] = _now()
                _write_json(status_path, doc)
            try:
                frac = float(doc.get("fraction"))
            except (TypeError, ValueError):
                frac = None
            count = record_film_crash(str(film), detail=detail, fraction=frac)
            if count >= MAX_FILM_CRASHES:
                stop_film_jobs(
                    str(film),
                    reason=(
                        f"Stopped after {count} crashes "
                        f"(last: {detail}). Diagnosing before retry."
                    ),
                )
            else:
                doc = read_job_status(status_path) or doc
                _requeue_status(
                    status_path,
                    doc,
                    reason=detail[:120],
                    count_stall=True,
                )
        finally:
            release_claim(status_path)
        return True

    # Dedicated worker (more RAM): child process + progress watchdog.
    ctx = mp.get_context("fork")
    proc = ctx.Process(
        target=_collect_child,
        args=(str(film), str(status_path)),
        name=f"statman-collect-{status_path.stem}",
        daemon=True,
    )
    proc.start()
    live = read_job_status(status_path) or status
    live["pid"] = int(proc.pid or 0)
    live["updated_at"] = _now()
    _write_json(status_path, live)

    progress_limit = stale_progress_seconds()
    try:
        while proc.is_alive():
            proc.join(timeout=5.0)
            if not proc.is_alive():
                break
            doc = read_job_status(status_path) or {}
            state = str(doc.get("state") or "").strip().lower()
            if state in {"done", "error"}:
                break
            age = job_age_seconds(doc)
            if age >= progress_limit:
                sys.stderr.write(
                    f"[statman-worker] hung {status_path.name} "
                    f"(no progress {int(age)}s) — killing pid={proc.pid}\n"
                )
                sys.stderr.flush()
                proc.terminate()
                proc.join(timeout=20)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=5)
                doc = read_job_status(status_path) or live
                detail = f"watchdog no progress {int(age)}s"
                frac = None
                try:
                    frac = float(doc.get("fraction"))
                except (TypeError, ValueError):
                    frac = None
                count = record_film_crash(str(film), detail=detail, fraction=frac)
                if count >= MAX_FILM_CRASHES:
                    stop_film_jobs(
                        str(film),
                        reason=(
                            f"Stopped after {count} crashes "
                            f"(last: {detail}). Diagnosing before retry."
                        ),
                    )
                else:
                    _requeue_status(
                        status_path,
                        doc,
                        reason=detail,
                        kill_pid=False,
                        count_stall=True,
                    )
                return True
        if proc.exitcode not in (0, None):
            doc = read_job_status(status_path) or live
            state = str(doc.get("state") or "").strip().lower()
            code = proc.exitcode
            if code in (-9, 137) or (isinstance(code, int) and code < 0):
                oom_hint = " (likely OOM / SIGKILL — lower STATMAN_SAMPLE_HZ)"
            else:
                oom_hint = ""
            if state == "error" and str(doc.get("error") or "").strip():
                detail = str(doc.get("error"))
            else:
                detail = f"Collect process exited with code {code}{oom_hint}"
                doc["error"] = detail
            frac = None
            try:
                frac = float(doc.get("fraction"))
            except (TypeError, ValueError):
                frac = None
            count = record_film_crash(str(film), detail=detail, fraction=frac)
            if count >= MAX_FILM_CRASHES:
                stop_film_jobs(
                    str(film),
                    reason=(
                        f"Stopped after {count} crashes "
                        f"(last: {detail}). Diagnosing before retry."
                    ),
                )
            else:
                doc["state"] = "queued"
                _requeue_status(
                    status_path,
                    doc,
                    reason=f"exit code {code}",
                    count_stall=True,
                )
    finally:
        release_claim(status_path)
    return True


def poll_once() -> bool:
    """Process the oldest queued job if any. Returns True when work ran."""

    reclaim_stale_jobs()
    paths = list_queued_job_paths()
    busy = _active_films()
    for path in paths:
        doc = read_job_status(path) or {}
        film = str(doc.get("film") or "").strip()
        if film and _film_key(film) in busy:
            sys.stdout.write(
                f"[statman-worker] defer {path.name} — same film already running\n"
            )
            sys.stdout.flush()
            continue
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
        doc = read_job_status(path) or {}
        state = str(doc.get("state") or "?")
        frac = doc.get("fraction")
        try:
            pct = f"{float(frac) * 100:.0f}%"
        except (TypeError, ValueError):
            pct = "?"
        age = int(job_age_seconds(doc))
        states.append(f"{path.name}:{state}@{pct}/{age}s")
    extra = "" if len(statuses) <= 12 else f" (+{len(statuses) - 12} more)"
    listing = ", ".join(states) if states else "(none)"
    return f"web_heartbeat={hb} · files={len(statuses)} [{listing}]{extra}"


def clear_film_crash_ledgers_once(folder: Path | None = None) -> int:
    """One-shot clear after the embed OOM fix so Salernitana can retry."""

    root = folder or collect_jobs_dir()
    if not root.is_dir():
        return 0
    marker = root / OOM_FIX_MARKER
    if marker.is_file():
        return 0
    removed = 0
    for path in root.glob(".crashes.*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    try:
        marker.write_text(_now(), encoding="utf-8")
    except OSError:
        pass
    if removed:
        sys.stdout.write(
            f"[statman-worker] cleared {removed} film crash ledger(s) after OOM fix\n"
        )
        sys.stdout.flush()
    return removed


def seed_film_crash_ledgers_from_errors(folder: Path | None = None) -> int:
    """Count existing error jobs so prior crashes count toward the 3-strike stop."""

    root = folder or collect_jobs_dir()
    if not root.is_dir():
        return 0
    by_film: dict[str, list[dict[str, Any]]] = {}
    for path in root.glob(f"*{JOB_SUFFIX}"):
        status = read_job_status(path)
        if status is None:
            continue
        if str(status.get("state") or "").strip().lower() != "error":
            continue
        err = str(status.get("error") or status.get("label") or "")
        if "duplicate" in err.lower() or "Stopped after" in err:
            continue
        film = str(status.get("film") or "").strip()
        if not film:
            continue
        by_film.setdefault(_film_key(film), []).append(status)
    seeded = 0
    for _key, statuses in by_film.items():
        film = str(statuses[0].get("film") or "")
        if film_crash_count(film, root) > 0:
            continue
        for status in statuses[:MAX_FILM_CRASHES]:
            try:
                frac = float(status.get("fraction"))
            except (TypeError, ValueError):
                frac = None
            record_film_crash(
                film,
                detail=str(status.get("error") or status.get("label") or "prior error"),
                fraction=frac,
                folder=root,
            )
            seeded += 1
    return seeded


def run_forever(*, poll_s: float | None = None) -> None:
    """Block forever, claiming queued Analyse jobs."""

    interval = worker_poll_seconds() if poll_s is None else max(0.5, float(poll_s))
    jobs = ensure_jobs_dir()
    cleared = clear_film_crash_ledgers_once(jobs)
    # After the OOM fix wipe, do not re-seed from the old exit-code-1 errors.
    if not cleared:
        seeded = seed_film_crash_ledgers_from_errors(jobs)
        if seeded:
            sys.stdout.write(
                f"[statman-worker] seeded {seeded} prior crash(es) into ledgers\n"
            )
            sys.stdout.flush()
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
