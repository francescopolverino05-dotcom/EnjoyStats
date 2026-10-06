"""Tests for Wyscout/Opta/StatsBomb event registry."""

from __future__ import annotations

from analytics.event_registry import (
    DataProvider,
    WYSCOUT_DANGER_X_MIN,
    event_definition,
    is_wyscout_danger_zone,
    provider_note,
)
from data_models.events import EventType


def test_registry_covers_core_open_play_events() -> None:
    for kind in (
        EventType.PASS,
        EventType.CROSS,
        EventType.SHOT,
        EventType.GOAL,
        EventType.INTERCEPTION,
        EventType.BALL_RECOVERY,
    ):
        row = event_definition(kind)
        assert row.summary
        assert row.wyscout or row.opta or row.statsbomb


def test_provider_notes_return_text() -> None:
    assert "Pass" in provider_note(EventType.PASS, DataProvider.WYSCOUT)
    assert "Cross" in provider_note(EventType.CROSS, DataProvider.OPTA)
    assert "Shot" in provider_note(EventType.SHOT, DataProvider.STATSBOMB)


def test_wyscout_danger_zone() -> None:
    assert is_wyscout_danger_zone(90.0, 50.0, attack_goal_x=100.0)
    assert not is_wyscout_danger_zone(50.0, 50.0, attack_goal_x=100.0)
    assert is_wyscout_danger_zone(
        100.0 - WYSCOUT_DANGER_X_MIN, 50.0, attack_goal_x=0.0
    )
