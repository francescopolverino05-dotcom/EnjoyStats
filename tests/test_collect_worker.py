"""Tests for the StatMan Analyse background worker."""

from __future__ import annotations

from pathlib import Path

from analytics.collect_job import read_job_status, start_collect_job
from analytics.collect_worker import (
    claim_job,
    list_queued_job_paths,
    poll_once,
    process_one,
    use_external_worker,
)
from analytics.video_auto_collect import write_synthetic_match_clip


def test_use_external_worker_flag(monkeypatch) -> None:
    monkeypatch.delenv("STATMAN_USE_EXTERNAL_WORKER", raising=False)
    assert use_external_worker() is False
    monkeypatch.setenv("STATMAN_USE_EXTERNAL_WORKER", "1")
    assert use_external_worker() is True


def test_external_worker_enqueues_without_spawning(
    tmp_path: Path, monkeypatch
) -> None:
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


def test_poll_once_no_work(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    assert poll_once() is False
