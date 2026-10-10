"""Off-ball / Influence IQ proxies from match tags."""

from __future__ import annotations

from uuid import uuid5

from analytics.game_ingest import collect_game
from analytics.offball_iq import (
    PHASE1_WINDOW_S,
    PHASE2_WINDOW_S,
    _influence_iq,
    _set_piece_tally,
    offball_player_rows,
    offball_player_stats,
    offball_team_rows,
    offball_team_stats,
)
from analytics.sample_game import (
    HOME_TEAM_ID,
    SIM_MATCH_ID,
    sample_game_payload,
)
from data_models.events import EventType, MatchEvent, ShotOutcome


def test_sample_game_has_offball_team_and_player_iq() -> None:
    rundown = collect_game(sample_game_payload())
    teams = offball_team_stats(rundown)
    assert len(teams) == 2
    assert all(0.0 <= team.influence_iq <= 100.0 for team in teams)
    assert sum(team.player_halos for team in teams) >= 1
    rows = offball_team_rows(teams)
    labels = [row["Stat"] for row in rows]
    assert "Influence IQ" in labels
    assert "Attacking Triangles" in labels
    assert "Pressing Ability" in labels
    assert "Set-Piece Deliveries" in labels
    assert "Set-Piece First Contact" in labels
    assert "Set-Piece Second Phase" in labels
    players = offball_player_stats(rundown)
    assert players
    assert players[0].influence_iq >= players[-1].influence_iq
    player_rows = offball_player_rows(players)
    assert "SP 1st" in player_rows[0]
    assert "SP 2nd" in player_rows[0]


def test_sample_game_set_pieces_boost_iq_board() -> None:
    rundown = collect_game(sample_game_payload())
    teams = offball_team_stats(rundown)
    home = next(team for team in teams if team.team_id == HOME_TEAM_ID)
    assert home.set_piece_deliveries >= 1
    assert home.set_piece_first_contact >= 1
    assert home.set_piece_second_phase >= 1
    players = offball_player_stats(rundown)
    assert any(row.set_piece_first_contact >= 1 for row in players)
    assert any(row.set_piece_second_phase >= 1 for row in players)


def test_set_piece_phase_windows_credit_first_contact_and_shot() -> None:
    taker = uuid5(SIM_MATCH_ID, "taker")
    jumper = uuid5(SIM_MATCH_ID, "jumper")
    shooter = uuid5(SIM_MATCH_ID, "shooter")
    corner = MatchEvent.model_validate(
        {
            "match_id": SIM_MATCH_ID,
            "team_id": HOME_TEAM_ID,
            "player_id": taker,
            "period": 1,
            "minute": 10,
            "second": 0,
            "event_type": EventType.CORNER,
            "x": 99.0,
            "y": 5.0,
            "end_x": 92.0,
            "end_y": 50.0,
            "successful": True,
        }
    )
    aerial = MatchEvent.model_validate(
        {
            "match_id": SIM_MATCH_ID,
            "team_id": HOME_TEAM_ID,
            "player_id": jumper,
            "period": 1,
            "minute": 10,
            "second": int(PHASE1_WINDOW_S) - 2,
            "event_type": EventType.AERIAL_DUEL,
            "x": 90.0,
            "y": 50.0,
            "successful": True,
        }
    )
    shot = MatchEvent.model_validate(
        {
            "match_id": SIM_MATCH_ID,
            "team_id": HOME_TEAM_ID,
            "player_id": shooter,
            "period": 1,
            "minute": 10,
            "second": int(PHASE2_WINDOW_S) - 5,
            "event_type": EventType.SHOT,
            "x": 88.0,
            "y": 48.0,
            "end_x": 100.0,
            "end_y": 50.0,
            "successful": False,
            "shot_outcome": ShotOutcome.MISSED,
        }
    )
    tally = _set_piece_tally([corner, aerial, shot], team_id=HOME_TEAM_ID)
    assert tally.deliveries == 1
    assert tally.first_contact == 1
    assert tally.second_phase == 1


def test_set_piece_terms_raise_influence_iq() -> None:
    base = _influence_iq(
        halos=5,
        triangles=2,
        recoveries=3,
        alleys=1,
        pressing=40.0,
        compact=40.0,
    )
    boosted = _influence_iq(
        halos=5,
        triangles=2,
        recoveries=3,
        alleys=1,
        pressing=40.0,
        compact=40.0,
        set_piece_deliveries=4,
        set_piece_first_contact=3,
        set_piece_second_phase=2,
    )
    assert boosted > base
