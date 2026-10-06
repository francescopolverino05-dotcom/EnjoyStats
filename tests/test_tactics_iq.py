"""Tests for EnjoyStats film tactical IQ (glossary + classifiers)."""

from __future__ import annotations

from analytics.tactics_iq import (
    GLOSSARY,
    PitchLane,
    classify_distribution,
    classify_strike,
    is_goalkeeper_actor,
    pitch_lane,
    progressive_from_half_space,
)
from data_models.events import EventType


def test_glossary_has_gk_rule_and_core_terms() -> None:
    assert "gk_rule" in GLOSSARY
    assert "half_space" in GLOSSARY
    assert "rest_defence" in GLOSSARY
    assert "shot" in GLOSSARY
    assert "goal" in GLOSSARY


def test_gk_rule_blocks_shots_and_goals() -> None:
    assert is_goalkeeper_actor("GK")
    assert is_goalkeeper_actor("", player_name="Napoli GK 1")
    verdict = classify_strike(
        start=(12.0, 50.0),
        point=(5.0, 50.0),
        travel=20.0,
        speed=30.0,
        attack_goal_x=100.0,
        shot_gap_ok=True,
        goal_gap_ok=True,
        gap_ok=True,
        actor_is_gk=True,
        pending_shot=False,
        pending_fresh=False,
    )
    assert not verdict.is_shot
    assert not verdict.is_goal
    assert verdict.reason == "gk_rule"


def test_central_strike_still_goals() -> None:
    verdict = classify_strike(
        start=(78.0, 50.0),
        point=(98.0, 50.0),
        travel=20.0,
        speed=30.0,
        attack_goal_x=100.0,
        shot_gap_ok=True,
        goal_gap_ok=True,
        gap_ok=True,
        actor_is_gk=False,
        pending_shot=False,
        pending_fresh=False,
    )
    assert verdict.is_goal
    assert verdict.on_target


def test_box_dribble_is_not_a_shot() -> None:
    verdict = classify_strike(
        start=(85.0, 45.0),
        point=(88.0, 48.0),
        travel=4.0,
        speed=5.0,
        attack_goal_x=100.0,
        shot_gap_ok=True,
        goal_gap_ok=True,
        gap_ok=True,
        actor_is_gk=False,
        pending_shot=False,
        pending_fresh=False,
    )
    assert not verdict.is_shot
    assert not verdict.is_goal


def test_half_space_and_cutback_classification() -> None:
    assert pitch_lane(30.0) is PitchLane.LEFT_HALF_SPACE
    assert pitch_lane(10.0) is PitchLane.LEFT_WING
    assert progressive_from_half_space(start_y=30.0, toward_goal=True, travel=12.0)
    cutback = classify_distribution(
        start=(94.0, 12.0),
        point=(88.0, 50.0),
        travel=40.0,
        toward_goal=False,
        in_box=True,
        touchline=False,
        corner=False,
        actor_is_gk=False,
    )
    assert cutback is EventType.CUTBACK
    cross = classify_distribution(
        start=(80.0, 8.0),
        point=(90.0, 50.0),
        travel=45.0,
        toward_goal=True,
        in_box=True,
        touchline=False,
        corner=False,
        actor_is_gk=False,
    )
    assert cross is EventType.CROSS
