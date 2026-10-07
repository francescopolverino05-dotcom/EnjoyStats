"""Shared /data jobs dir selection for Railway Web + Worker."""

from __future__ import annotations

from pathlib import Path

from analytics.collect_job import collect_jobs_dir
from analytics.video_auto_collect import film_inbox_dir, film_upload_dir


def test_collect_jobs_dir_prefers_shared_data_volume(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "jobs").mkdir()
    monkeypatch.setenv("STATMAN_DATA_ROOT", str(data))
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "elsewhere" / "jobs"))
    assert collect_jobs_dir() == data / "jobs"


def test_collect_jobs_dir_keeps_override_under_data(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    custom = data / "custom-jobs"
    custom.mkdir(parents=True)
    monkeypatch.setenv("STATMAN_DATA_ROOT", str(data))
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(custom))
    assert collect_jobs_dir() == custom


def test_film_dirs_prefer_shared_data(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("STATMAN_DATA_ROOT", str(data))
    monkeypatch.delenv("ENJOYSTATS_FILM_INBOX", raising=False)
    monkeypatch.delenv("ENJOYSTATS_FILM_UPLOADS", raising=False)
    assert film_inbox_dir() == data / "inbox"
    assert film_upload_dir() == data / "uploads"
