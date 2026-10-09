"""Tests for StatMan IQ (glossary + classifiers)."""

from __future__ import annotations

from uuid import uuid4

from analytics.statman_iq import (
    GLOSSARY,
    IQ_SOURCES,
    PitchLane,
    classify_distribution,
    classify_strike,
    is_cross_flank,
    is_goalkeeper_actor,
    pitch_lane,
    progressive_from_half_space,
    sanitize_film_goals,
)
from data_models.events import EventType, MatchEvent, ShotOutcome


def test_glossary_has_gk_rule_and_core_terms() -> None:
    assert "gk_rule" in GLOSSARY
    assert "half_space" in GLOSSARY
    assert "rest_defence" in GLOSSARY
    assert "shot" in GLOSSARY
    assert "goal" in GLOSSARY
    assert "danger_zone" in GLOSSARY
    assert "pass" in GLOSSARY
    assert "cross" in GLOSSARY
    assert "distinti_score" in GLOSSARY
    assert any("wyscout.com" in url for _title, url in IQ_SOURCES)
    assert any("opta-event" in url for _title, url in IQ_SOURCES)
    assert any("coachesvoice.com" in url for _title, url in IQ_SOURCES)
    assert any("spielverlagerung.com" in url for _title, url in IQ_SOURCES)


def test_wyscout_cross_requires_flank_origin() -> None:
    assert is_cross_flank(10.0)
    assert is_cross_flank(90.0)
    assert not is_cross_flank(50.0)
    cross = classify_distribution(
        start=(80.0, 10.0),
        point=(90.0, 50.0),
        travel=45.0,
        toward_goal=True,
        in_box=True,
        touchline=False,
        corner=False,
        actor_is_gk=False,
    )
    assert cross is EventType.CROSS
    centre_pass = classify_distribution(
        start=(80.0, 50.0),
        point=(90.0, 50.0),
        travel=20.0,
        toward_goal=True,
        in_box=True,
        touchline=False,
        corner=False,
        actor_is_gk=False,
    )
    assert centre_pass is EventType.PASS


def test_sanitize_film_goals_demotes_excess(monkeypatch) -> None:
    monkeypatch.setenv("STATMAN_MAX_GOALS", "2")
    monkeypatch.setenv("STATMAN_MAX_GOALS_PER_TEAM", "10")
    match_id = uuid4()
    team_id = uuid4()
    player_id = uuid4()
    events = [
        MatchEvent.model_validate(
            {
                "match_id": match_id,
                "team_id": team_id,
                "player_id": player_id,
                "period": 1,
                "minute": i,
                "second": 0,
                "event_type": EventType.GOAL,
                "is_goal": True,
                "shot_outcome": ShotOutcome.ON_TARGET,
                "x": 90.0,
                "y": 50.0,
            }
        )
        for i in range(5)
    ]
    cleaned = sanitize_film_goals(events)
    goals = [event for event in cleaned if event.is_goal or event.event_type is EventType.GOAL]
    shots = [event for event in cleaned if event.event_type is EventType.SHOT]
    assert len(goals) == 2
    assert len(shots) == 3


def test_sanitize_film_goals_caps_per_team(monkeypatch) -> None:
    monkeypatch.setenv("STATMAN_MAX_GOALS", "20")
    monkeypatch.setenv("STATMAN_MAX_GOALS_PER_TEAM", "3")
    match_id = uuid4()
    home = uuid4()
    away = uuid4()
    player = uuid4()
    events = []
    for i in range(8):
        events.append(
            MatchEvent.model_validate(
                {
                    "match_id": match_id,
                    "team_id": home,
                    "player_id": player,
                    "period": 1,
                    "minute": i,
                    "second": 0,
                    "event_type": EventType.GOAL,
                    "is_goal": True,
                    "shot_outcome": ShotOutcome.ON_TARGET,
                    "x": 90.0,
                    "y": 50.0,
                }
            )
        )
    for i in range(5):
        events.append(
            MatchEvent.model_validate(
                {
                    "match_id": match_id,
                    "team_id": away,
                    "player_id": player,
                    "period": 2,
                    "minute": i,
                    "second": 0,
                    "event_type": EventType.GOAL,
                    "is_goal": True,
                    "shot_outcome": ShotOutcome.ON_TARGET,
                    "x": 10.0,
                    "y": 50.0,
                }
            )
        )
    cleaned = sanitize_film_goals(events)
    home_goals = sum(
        1 for e in cleaned if e.team_id == home and (e.is_goal or e.event_type is EventType.GOAL)
    )
    away_goals = sum(
        1 for e in cleaned if e.team_id == away and (e.is_goal or e.event_type is EventType.GOAL)
    )
    assert home_goals == 3
    assert away_goals == 3


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


def test_central_strike_without_pending_is_shot_not_goal() -> None:
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
    assert verdict.is_shot
    assert not verdict.is_goal
    assert verdict.on_target


def test_pending_shot_to_mouth_is_goal() -> None:
    verdict = classify_strike(
        start=(90.0, 50.0),
        point=(98.0, 50.0),
        travel=8.0,
        speed=20.0,
        attack_goal_x=100.0,
        shot_gap_ok=True,
        goal_gap_ok=True,
        gap_ok=True,
        actor_is_gk=False,
        pending_shot=True,
        pending_fresh=True,
    )
    assert verdict.is_goal
    assert verdict.on_target
    assert verdict.reason == "pending_shot_to_mouth"


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
