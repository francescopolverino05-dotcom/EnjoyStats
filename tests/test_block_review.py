"""Tests for 5-minute block coverage (Step C) and Review edits (Step D)."""

from __future__ import annotations

from uuid import uuid4

from analytics.block_coverage import (
    BLOCK_MIN_EVENTS,
    BLOCK_SECONDS,
    block_index_for_clock,
    blocks_needing_repass,
    build_coverage_report,
    expected_block_count,
    filter_events_outside_block,
)
from analytics.game_ingest import collect_game
from analytics.review_edits import apply_review_edits, coverage_rows_for_rundown, review_rows
from analytics.sample_game import sample_game_payload
from analytics.video_auto_collect import collect_from_video, write_synthetic_match_clip
from data_models.events import EventType, MatchEvent, ShotOutcome


def test_block_math() -> None:
    assert BLOCK_SECONDS == 300
    assert BLOCK_MIN_EVENTS == 30
    assert block_index_for_clock(0) == 0
    assert block_index_for_clock(299) == 0
    assert block_index_for_clock(300) == 1
    assert expected_block_count(90) == 18


def test_sparse_blocks_need_repass() -> None:
    match_id = uuid4()
    team = uuid4()
    events = [
        MatchEvent(
            match_id=match_id,
            team_id=team,
            minute=1,
            second=0,
            event_type=EventType.PASS,
            x=40,
            y=50,
            end_x=50,
            end_y=50,
        )
    ]
    coverage = build_coverage_report(events, duration_minutes=10, home_team_ids={team})
    assert len(coverage) == 2
    assert coverage[0].needs_repass is True
    assert blocks_needing_repass(coverage) == [0, 1]


def test_filter_events_outside_block() -> None:
    match_id = uuid4()
    team = uuid4()
    early = MatchEvent(
        match_id=match_id,
        team_id=team,
        minute=1,
        event_type=EventType.PASS,
        x=40,
        y=50,
        end_x=50,
        end_y=50,
    )
    late = MatchEvent(
        match_id=match_id,
        team_id=team,
        minute=6,
        event_type=EventType.PASS,
        x=40,
        y=50,
        end_x=50,
        end_y=50,
    )
    kept = filter_events_outside_block([early, late], 0)
    assert kept == [late]


def test_review_delete_and_retarget() -> None:
    rundown = collect_game(sample_game_payload())
    rows = review_rows(rundown)
    assert rows
    # Drop the first tag.
    rows[0]["keep"] = False
    # Change second tag type if possible.
    if len(rows) > 1:
        rows[1]["tag"] = EventType.CROSS.value
        if rundown.events[1].end_x is None:
            # CROSS needs ends — apply_review_edits fills them.
            pass
    updated = apply_review_edits(rundown, rows)
    assert updated.summary.event_count == rundown.summary.event_count - 1
    coverage = coverage_rows_for_rundown(updated)
    assert coverage
    assert "status" in coverage[0]


def test_film_collect_writes_coverage(tmp_path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "blocks.avi")
    rundown = collect_from_video(clip, sample_hz=8.0, max_sample_frames=24)
    assert rundown.events
    coverage_path = tmp_path / "blocks.coverage.json"
    assert coverage_path.is_file()
    text = coverage_path.read_text(encoding="utf-8")
    assert "block_seconds" in text
    assert "300" in text
