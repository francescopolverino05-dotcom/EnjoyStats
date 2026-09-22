"""Background full-match collect job."""

from __future__ import annotations

from pathlib import Path

from analytics.collect_job import latest_job_status_path, read_job_status, run_collect_job
from analytics.video_auto_collect import write_synthetic_match_clip


def test_run_collect_job_writes_status_and_rundown(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "Home_-_Away.avi", frames=24, fps=8)
    status_path = tmp_path / "job.status.json"
    rundown = run_collect_job(clip, status_path)
    assert rundown.summary.event_count >= 1
    status = read_job_status(status_path)
    assert status is not None
    assert status["state"] == "done"
    assert Path(str(status["rundown_path"])).is_file()
    assert rundown.summary.passes + rundown.summary.shots >= 1


def test_latest_job_status_path_is_newest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path))
    older = tmp_path / "aaaa.status.json"
    newer = tmp_path / "bbbb.status.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")
    assert latest_job_status_path() == newer
