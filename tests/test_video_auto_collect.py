"""Tests for match-film auto-collection (no manual event tags)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from analytics.video_auto_collect import (
    MAX_VIDEO_BYTES,
    MAX_VIDEO_GIB,
    VIDEO_SUFFIXES,
    video_limit_label,
    Track,
    VideoCollectError,
    collect_from_video,
    events_from_tracks,
    list_ready_films,
    normalize_film_path,
    probe_video,
    remux_for_opencv,
    safe_film_name,
    stitch_tracks,
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
    sheet = inbox / "arsenal.xml"
    sheet.write_text("<analysis/>", encoding="utf-8")
    found = {path.resolve() for path in list_ready_films(inbox)}
    assert found == {keep.resolve(), sheet.resolve()}


def test_film_size_cap_is_five_gigabytes() -> None:
    assert MAX_VIDEO_GIB == 5
    assert MAX_VIDEO_BYTES == 5 * 1024 * 1024 * 1024
    assert video_limit_label() == "5 GB"


def test_write_film_chunks_rejects_oversize(tmp_path: Path) -> None:
    dest = tmp_path / "too-big.mp4"
    with pytest.raises(VideoCollectError, match="upload limit"):
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


def test_stitch_tracks_joins_fragmented_identities() -> None:
    first = Track(
        track_id=1,
        kind="player",
        xs=[30.0, 31.0, 32.0],
        ys=[50.0, 50.0, 50.0],
        frames=[0, 1, 2],
        last_x=32.0,
        last_y=50.0,
        bgr=(20.0, 40.0, 200.0),
    )
    second = Track(
        track_id=2,
        kind="player",
        xs=[33.0, 34.0, 35.0],
        ys=[50.0, 50.0, 50.0],
        frames=[6, 7, 8],
        last_x=35.0,
        last_y=50.0,
        bgr=(22.0, 42.0, 198.0),
    )
    joined = stitch_tracks([first, second], max_gap_frames=10, max_join_dist=20.0)
    players = [track for track in joined if track.kind == "player"]
    assert len(players) == 1
    assert players[0].frames == [0, 1, 2, 6, 7, 8]


def test_static_crowd_does_not_hide_a_full_possession_chain() -> None:
    """Long-lived stand blobs used to crowd out the 22-track cap (≈9 tags)."""

    from uuid import uuid4

    from data_models.events import EventType

    n_frames = 480
    tracks: list[Track] = []
    for index in range(22):
        x = 8.0 + index * 3.5
        tracks.append(
            Track(
                track_id=index + 1,
                kind="player",
                xs=[x] * n_frames,
                ys=[12.0] * n_frames,
                frames=list(range(n_frames)),
                last_x=x,
                last_y=12.0,
                bgr=(20.0, 40.0, 200.0) if index < 11 else (200.0, 80.0, 20.0),
                team=0 if index < 11 else 1,
            )
        )
    fragments: list[Track] = []
    for start in range(0, n_frames, 5):
        xs = [25.0 + (start + offset) * 0.12 for offset in range(5)]
        fragments.append(
            Track(
                track_id=100 + start,
                kind="player",
                xs=xs,
                ys=[50.0] * 5,
                frames=list(range(start, start + 5)),
                last_x=xs[-1],
                last_y=50.0,
                bgr=(18.0, 36.0, 210.0),
                team=0,
            )
        )
    ball_xs = [28.0 + frame * 0.12 for frame in range(n_frames)]
    ball = Track(
        track_id=999,
        kind="ball",
        xs=ball_xs,
        ys=[50.0] * n_frames,
        frames=list(range(n_frames)),
        last_x=ball_xs[-1],
        last_y=50.0,
    )
    events, roster = events_from_tracks(
        [*tracks, *fragments, ball],
        fps=8.0,
        match_id=uuid4(),
        team_id=uuid4(),
        clip_url="file:///tmp/full.avi",
        home_name="Arsenal",
        away_name="Palace",
    )
    clocks = [event.video_timestamp_ms for event in events]
    assert roster
    assert len(events) >= 6
    assert max(clocks) >= 40_000
    assert any(
        event.event_type in {EventType.PASS, EventType.CROSS, EventType.SHOT, EventType.GOAL}
        for event in events
    )


def test_collect_film_uses_sibling_wyscout_xml(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "Arsenal_v_Palace.avi", frames=24, fps=8)
    shutil.copy(
        Path(__file__).resolve().parent / "fixtures" / "arsenal_v_palace_1-1.xml",
        tmp_path / "Arsenal_v_Palace.xml",
    )
    rundown = collect_from_film_path(clip)
    assert rundown.summary.goals == 2
    assert rundown.summary.passes >= 300
    names = {profile.player_name for profile in rundown.players}
    assert "A. Harriman-Annous" in names
