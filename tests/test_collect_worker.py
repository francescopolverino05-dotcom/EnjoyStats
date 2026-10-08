"""Tests for the StatMan Analyse background worker."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from analytics.collect_job import read_job_status, start_collect_job
from analytics.collect_worker import (
    claim_job,
    list_queued_job_paths,
    poll_once,
    process_one,
    reclaim_stale_jobs,
    use_external_worker,
)
from analytics.video_auto_collect import write_synthetic_match_clip


def test_use_external_worker_flag(monkeypatch) -> None:
    monkeypatch.delenv("STATMAN_USE_EXTERNAL_WORKER", raising=False)
    assert use_external_worker() is False
    monkeypatch.setenv("STATMAN_USE_EXTERNAL_WORKER", "1")
    assert use_external_worker() is True


def test_external_worker_enqueues_without_spawning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("STATMAN_USE_EXTERNAL_WORKER", "1")
    clip = write_synthetic_match_clip(tmp_path / "match.avi", frames=16, fps=8)
    status_path = start_collect_job(clip, home_team_name="Home", away_team_name="Away")
    status = read_job_status(status_path)
    assert status is not None
    assert status["state"] == "queued"
    assert "worker" in str(status["label"]).lower()
    assert list_queued_job_paths() == [status_path]


def test_worker_claims_and_runs_queued_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("STATMAN_USE_EXTERNAL_WORKER", "1")
    clip = write_synthetic_match_clip(tmp_path / "Home_-_Away.avi", frames=20, fps=8)
    status_path = start_collect_job(clip)
    assert process_one(status_path) is True
    status = read_job_status(status_path)
    assert status is not None
    assert status["state"] == "done"
    assert Path(str(status["rundown_path"])).is_file()


def test_claim_is_exclusive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    status_path = tmp_path / "abc.status.json"
    status_path.write_text(
        '{"state":"queued","film":"x.mp4","label":"q","fraction":0}',
        encoding="utf-8",
    )
    first = claim_job(status_path)
    assert first is not None
    assert first["state"] == "claimed"
    second = claim_job(status_path)
    assert second is None


def test_stale_running_job_reclaimed_even_if_pid_alive(
    tmp_path: Path, monkeypatch
) -> None:
    """Frozen progress (stuck at ~29%) must re-queue even when pid looks alive."""

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    monkeypatch.setenv("STATMAN_STALE_PROGRESS_S", "120")
    status_path = tmp_path / "salernitana.status.json"
    status_path.write_text(
        (
            '{"state":"running","film":"x.mp4","label":"Watching minute 12",'
            '"fraction":0.29,"pid":1,"stall_restarts":0,'
            '"updated_at":"2020-01-01T00:00:00+00:00"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "analytics.collect_worker._pid_alive",
        lambda _pid: True,
    )
    monkeypatch.setattr("analytics.collect_worker._kill_pid", lambda _pid: None)
    assert reclaim_stale_jobs() >= 1
    status = read_job_status(status_path)
    assert status is not None
    assert status["state"] == "queued"
    assert int(status["stall_restarts"]) == 1


def test_stale_claim_lock_is_reclaimed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    status_path = tmp_path / "stuck.status.json"
    status_path.write_text(
        '{"state":"queued","film":"x.mp4","label":"q","fraction":0,"pid":0,'
        '"updated_at":"2020-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    lock = status_path.with_suffix(status_path.suffix + ".claim")
    lock.write_text("pid=1\n", encoding="utf-8")
    old = time.time() - 10_000
    os_utime = __import__("os").utime
    os_utime(lock, (old, old))
    assert list_queued_job_paths() == []
    assert reclaim_stale_jobs() >= 1
    assert list_queued_job_paths() == [status_path]


def test_missing_film_marks_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("STATMAN_USE_EXTERNAL_WORKER", "1")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    status_path = jobs / "nofilm.status.json"
    status_path.write_text(
        '{"state":"queued","film":"/no/such/film.mp4","label":"q","fraction":0,"pid":0}',
        encoding="utf-8",
    )
    assert process_one(status_path) is True
    status = read_job_status(status_path)
    assert status is not None
    assert status["state"] == "error"
    assert "not found" in str(status["error"]).lower()


def test_collect_in_process_defaults_for_embed(monkeypatch) -> None:
    from analytics.collect_worker import collect_in_process

    monkeypatch.delenv("STATMAN_COLLECT_INPROCESS", raising=False)
    monkeypatch.setenv("STATMAN_EMBED_WORKER", "1")
    assert collect_in_process() is True
    monkeypatch.setenv("STATMAN_COLLECT_INPROCESS", "0")
    assert collect_in_process() is False


def test_film_stops_after_three_crashes(tmp_path: Path, monkeypatch) -> None:
    from analytics.collect_worker import (
        MAX_FILM_CRASHES,
        film_crash_count,
        record_film_crash,
        stop_film_jobs,
    )

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    film = tmp_path / "match.mp4"
    film.write_bytes(b"x")
    for i in range(MAX_FILM_CRASHES):
        assert record_film_crash(str(film), detail=f"boom{i}", fraction=0.2) == i + 1
    assert film_crash_count(str(film)) == MAX_FILM_CRASHES
    status_path = tmp_path / "z.status.json"
    status_path.write_text(
        (
            f'{{"state":"queued","film":"{film}","label":"q","fraction":0,"pid":0}}'
        ),
        encoding="utf-8",
    )
    assert stop_film_jobs(str(film), reason="Stopped after 3 crashes") == 1
    status = read_job_status(status_path)
    assert status is not None
    assert status["state"] == "error"
    assert "3 crashes" in str(status["error"])


def test_poll_once_defers_duplicate_film(tmp_path: Path, monkeypatch) -> None:
    import json
    from datetime import datetime, timezone

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    film = tmp_path / "same.mp4"
    film.write_bytes(b"x")
    now = datetime.now(timezone.utc).isoformat()
    running = tmp_path / "a.status.json"
    running.write_text(
        json.dumps(
            {
                "state": "running",
                "film": str(film),
                "label": "w",
                "fraction": 0.2,
                "pid": 9,
                "updated_at": now,
            }
        ),
        encoding="utf-8",
    )
    queued = tmp_path / "b.status.json"
    queued.write_text(
        json.dumps(
            {
                "state": "queued",
                "film": str(film),
                "label": "q",
                "fraction": 0,
                "pid": 0,
            }
        ),
        encoding="utf-8",
    )
    assert poll_once() is False
    status = read_job_status(queued)
    assert status is not None
    assert status["state"] == "queued"


def test_poll_once_no_work(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    assert poll_once() is False


def test_ensure_jobs_dir_raises_when_unwritable(tmp_path: Path, monkeypatch) -> None:
    from analytics.collect_worker import ensure_jobs_dir

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o555)
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(blocked / "jobs"))
    try:
        with pytest.raises(OSError):
            ensure_jobs_dir()
    finally:
        blocked.chmod(0o755)
