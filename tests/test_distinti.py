"""Tests for Italian distinti / tabellino parsing and score pinning."""

from __future__ import annotations

from uuid import uuid4

from analytics.distinti import (
    apply_official_score,
    facts_to_payload,
    merge_lineups_with_facts,
    parse_distinti_text,
)
from analytics.lineups import LineupPlayer, MatchLineups
from data_models.events import EventType, MatchEvent, ShotOutcome


SAMPLE_DISTINTI = """
Campionato Primavera 2 - Girone B
SALERNITANA - BARI
Risultato: 0-4

Squadra casa
1 Cataldo
2 Rossi
9 Valentino

Squadra ospite
1 Cipolla
7 Dimonte
10 Italia
11 Alonso Campagna
"""


def test_parse_distinti_score_and_players() -> None:
    facts = parse_distinti_text(SAMPLE_DISTINTI, source_label="sample.txt")
    assert facts.home_goals == 0
    assert facts.away_goals == 4
    assert facts.score_label() == "0-4"
    assert len(facts.home) >= 2
    assert len(facts.away) >= 2
    assert facts.home[0].jersey == 1
    assert "Cataldo" in facts.home[0].name
    payload = facts_to_payload(facts)
    assert payload["home_goals"] == 0
    assert payload["away_goals"] == 4


def test_clean_film_rundown_cuts_shot_noise(monkeypatch) -> None:
    monkeypatch.setenv("STATMAN_MAX_SHOTS", "6")
    monkeypatch.setenv("STATMAN_MAX_SHOTS_PER_TEAM", "6")
    monkeypatch.setenv("STATMAN_MAX_GOALS", "2")
    monkeypatch.setenv("STATMAN_MAX_GOALS_PER_TEAM", "2")
    from analytics.distinti import clean_film_rundown
    from analytics.game_ingest import GamePayload, PlayerRosterEntry, collect_game

    match_id = uuid4()
    home_id = uuid4()
    player = uuid4()
    events = [
        MatchEvent.model_validate(
            {
                "match_id": match_id,
                "team_id": home_id,
                "player_id": player,
                "period": 1,
                "minute": i % 45,
                "second": 0,
                "event_type": EventType.SHOT,
                "is_goal": False,
                "shot_outcome": ShotOutcome.MISSED,
                "x": 88.0,
                "y": 50.0,
            }
        )
        for i in range(40)
    ]
    rundown = collect_game(
        GamePayload(
            match_id=match_id,
            players=[
                PlayerRosterEntry(
                    player_id=player,
                    team_id=home_id,
                    jersey_number=9,
                    player_name="Striker",
                    position="ST",
                )
            ],
            events=events,
            home_team_name="Home",
            away_team_name="Away",
            tag_source="film",
        )
    )
    assert rundown.summary.shots == 40
    cleaned = clean_film_rundown(rundown)
    assert cleaned.summary.shots <= 6


def test_pin_rundown_score_fixes_bogus_film_score() -> None:
    from analytics.distinti import pin_rundown_score
    from analytics.game_ingest import GamePayload, PlayerRosterEntry, collect_game

    match_id = uuid4()
    home_id = uuid4()
    away_id = uuid4()
    home_player = uuid4()
    away_player = uuid4()
    events = []
    for i in range(10):
        events.append(
            MatchEvent.model_validate(
                {
                    "match_id": match_id,
                    "team_id": home_id,
                    "player_id": home_player,
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
    for i in range(6):
        events.append(
            MatchEvent.model_validate(
                {
                    "match_id": match_id,
                    "team_id": away_id,
                    "player_id": away_player,
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
    rundown = collect_game(
        GamePayload(
            match_id=match_id,
            players=[
                PlayerRosterEntry(
                    player_id=home_player,
                    team_id=home_id,
                    jersey_number=9,
                    player_name="Home Striker",
                    position="ST",
                ),
                PlayerRosterEntry(
                    player_id=away_player,
                    team_id=away_id,
                    jersey_number=10,
                    player_name="Away Striker",
                    position="ST",
                ),
            ],
            events=events,
            home_team_name="Salernitana",
            away_team_name="Bari",
            tag_source="film",
        )
    )
    assert rundown.summary.goals == 16
    pinned = pin_rundown_score(rundown, home_goals=0, away_goals=4)
    assert pinned.summary.goals == 4
    home_goals = sum(
        1
        for event in pinned.events
        if event.team_id == home_id and (event.is_goal or event.event_type is EventType.GOAL)
    )
    away_goals = sum(
        1
        for event in pinned.events
        if event.team_id == away_id and (event.is_goal or event.event_type is EventType.GOAL)
    )
    assert home_goals == 0
    assert away_goals == 4


def test_apply_official_score_demotes_extra_goals() -> None:
    match_id = uuid4()
    home_id = uuid4()
    away_id = uuid4()
    player = uuid4()
    events = []
    for minute, team in [(10, home_id), (20, away_id), (30, away_id), (40, away_id), (50, away_id)]:
        events.append(
            MatchEvent.model_validate(
                {
                    "match_id": match_id,
                    "team_id": team,
                    "player_id": player,
                    "period": 1 if minute < 45 else 2,
                    "minute": minute % 45,
                    "second": 0,
                    "event_type": EventType.GOAL,
                    "is_goal": True,
                    "shot_outcome": ShotOutcome.ON_TARGET,
                    "x": 95.0,
                    "y": 50.0,
                }
            )
        )
    cleaned = apply_official_score(
        events,
        home_team_id=home_id,
        away_team_id=away_id,
        home_goals=0,
        away_goals=2,
    )
    goals = [e for e in cleaned if e.is_goal or e.event_type is EventType.GOAL]
    shots = [e for e in cleaned if e.event_type is EventType.SHOT]
    assert len(goals) == 2
    assert all(g.team_id == away_id for g in goals)
    assert len(shots) == 3


def test_merge_prefers_csv_jerseys() -> None:
    csv_lineups = MatchLineups(
        home_team="Salernitana",
        away_team="Bari",
        home=(LineupPlayer("home", 9, "CSV Nine", "ST"),),
        away=(),
    )
    facts = parse_distinti_text(SAMPLE_DISTINTI)
    merged = merge_lineups_with_facts(csv_lineups, facts)
    assert merged is not None
    assert merged.home[0].name == "CSV Nine"
    assert len(merged.away) >= 1
