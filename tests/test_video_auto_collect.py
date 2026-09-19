"""Tests for match-film auto-collection (no manual event tags)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from analytics.video_auto_collect import (
    MAX_VIDEO_BYTES,
    VIDEO_SUFFIXES,
    VideoCollectError,
    collect_from_video,
    list_ready_films,
    normalize_film_path,
    probe_video,
    remux_for_opencv,
    safe_film_name,
    write_film_chunks,
    write_synthetic_match_clip,
)
from app.ingest import collect_from_film_path, ready_films, save_uploaded_film


def test_normalize_film_path_strips_quotes_and_file_uri(tmp_path: Path) -> None:
    clip = tmp_path / "derby.mp4"
    clip.write_bytes(b"x")
    quoted = f'"{clip}"'
    assert normalize_film_path(quoted) == clip
    assert normalize_film_path(f"'{clip}'") == clip
    assert normalize_film_path(clip.as_uri()) == clip
    assert safe_film_name("../../evil.mov") == "evil.mov"
    assert safe_film_name("Match Day.MP4") == "Match_Day.MP4"
    assert safe_film_name("notes").endswith(".mp4")


def test_list_ready_films_ignores_empty_and_non_video(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "notes.txt").write_text("nope", encoding="utf-8")
    (inbox / "empty.mp4").write_bytes(b"")
    keep = inbox / "keep.mov"
    keep.write_bytes(b"film")
    found = list_ready_films(inbox)
    assert found == [keep.resolve()]


def test_write_film_chunks_rejects_oversize(tmp_path: Path) -> None:
    dest = tmp_path / "too-big.mp4"
    with pytest.raises(VideoCollectError, match="3 GB"):
        write_film_chunks(dest, [b"abc", b"def"], max_bytes=4)


def test_collect_from_quoted_film_path(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "quoted.avi")
    rundown = collect_from_film_path(f'"{clip}"')
    assert rundown.players


def test_ready_films_creates_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = tmp_path / "inbox"
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(inbox))
    monkeypatch.setenv("ENJOYSTATS_FILM_UPLOADS", str(uploads))
    clip = write_synthetic_match_clip(inbox / "drop.avi")
    found = ready_films()
    assert clip.resolve() in found


def test_probe_rejects_missing_and_unsupported(tmp_path: Path) -> None:
    missing = tmp_path / "nope.mp4"
    with pytest.raises(VideoCollectError, match="not found"):
        probe_video(missing)
    text = tmp_path / "notes.txt"
    text.write_text("not a film", encoding="utf-8")
    with pytest.raises(VideoCollectError, match="Unsupported"):
        probe_video(text)


def test_synthetic_film_collects_stats_without_tag_json(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "clip.avi", frames=24, fps=8)
    info = probe_video(clip)
    assert info.size_bytes > 0
    assert info.size_bytes < MAX_VIDEO_BYTES
    assert clip.suffix.lower() in VIDEO_SUFFIXES
    rundown = collect_from_video(clip, sample_hz=8.0, max_sample_frames=24)
    assert rundown.summary.player_count >= 1
    assert rundown.summary.event_count >= 1
    assert rundown.summary.passes + rundown.summary.shots >= 1
    names = {profile.player_name for profile in rundown.players}
    assert any("Home" in name or "Away" in name or name.startswith("Player ") for name in names)


def test_collect_from_film_path_wrapper(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "match.avi")
    rundown = collect_from_film_path(clip)
    assert rundown.players
    with pytest.raises(ValueError, match="match film"):
        collect_from_film_path(tmp_path / "events.json")


def test_save_uploaded_film_chunks(tmp_path: Path) -> None:
    dest = tmp_path / "upload.mp4"

    class _Chunks:
        def __init__(self) -> None:
            self._chunks = [b"abc", b"def", b""]
            self._i = 0

        def seek(self, _offset: int) -> None:
            self._i = 0

        def read(self, _size: int) -> bytes:
            if self._i >= len(self._chunks):
                return b""
            chunk = self._chunks[self._i]
            self._i += 1
            return chunk

    saved = save_uploaded_film(_Chunks(), dest)
    assert saved.read_bytes() == b"abcdef"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required to remux")
def test_remux_synthetic_clip_is_readable(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "clip.avi", frames=8, fps=8)
    remuxed = remux_for_opencv(clip)
    assert remuxed.is_file()
    info = probe_video(remuxed)
    assert info.size_bytes > 0
    assert info.frame_count >= 1


def test_progress_callbacks_fire_during_save_and_collect(tmp_path: Path) -> None:
    dest = tmp_path / "upload.mp4"
    writes: list[tuple[int, int]] = []

    class _Sized:
        size = 6

        def __init__(self) -> None:
            self._data = b"abcdef"
            self._offset = 0

        def seek(self, offset: int) -> None:
            self._offset = offset

        def read(self, size: int) -> bytes:
            chunk = self._data[self._offset : self._offset + size]
            self._offset += len(chunk)
            return chunk

    save_uploaded_film(_Sized(), dest, on_progress=lambda done, total: writes.append((done, total)))
    assert writes
    assert writes[-1] == (6, 6)

    clip = write_synthetic_match_clip(tmp_path / "clip.avi", frames=16, fps=8)
    stages: list[str] = []
    fractions: list[float] = []

    def _on_progress(label: str, fraction: float) -> None:
        stages.append(label)
        fractions.append(fraction)

    rundown = collect_from_video(
        clip, sample_hz=8.0, max_sample_frames=16, on_progress=_on_progress
    )
    assert rundown.summary.event_count >= 1
    assert any("Opening" in stage or "Watching" in stage for stage in stages)
    assert fractions[0] >= 0.0
    assert fractions[-1] == 1.0
