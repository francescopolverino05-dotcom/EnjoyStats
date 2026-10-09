"""Runtime checks for stride-aware shot speed and checkpoint cleanup."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from analytics.video_auto_collect import (
    Track,
    collect_from_video,
    events_from_tracks,
    film_checkpoint_path,
    write_synthetic_match_clip,
)
from data_models.events import EventType


def test_sampled_stride_does_not_inflate_shot_speed() -> None:
    """Ordinary 4-unit steps over 4 native frames must not become shots."""

    # fps=8, samples every 4 frames → true dt=0.5s, step≈4 → speed≈8 (<< 32).
    # Old bug used dt=1/8 → speed≈32 and fired fake shots.
    attacker = Track(
        track_id=1,
        kind="player",
        xs=[40.0, 44.0, 48.0, 52.0, 56.0, 60.0],
        ys=[50.0] * 6,
        frames=[0, 4, 8, 12, 16, 20],
        last_x=60.0,
        last_y=50.0,
        bgr=(20.0, 40.0, 200.0),
        team=0,
    )
    events, _ = events_from_tracks(
        [attacker],
        fps=8.0,
        match_id=uuid4(),
        team_id=uuid4(),
        clip_url="file:///tmp/midfield.avi",
        home_name="Home",
        away_name="Away",
    )
    shots = [e for e in events if e.event_type in {EventType.SHOT, EventType.GOAL} or e.is_goal]
    assert shots == []


def test_successful_collect_clears_resume_checkpoint(tmp_path: Path) -> None:
    film = write_synthetic_match_clip(tmp_path / "done.mp4", frames=32, fps=8)
    rundown = collect_from_video(film, force_fresh=True)
    assert rundown.summary.event_count >= 1
    assert not film_checkpoint_path(film).is_file()
