"""Off-ball / Influence IQ proxies from match tags."""

from __future__ import annotations

from analytics.game_ingest import collect_game
from analytics.offball_iq import (
    offball_player_stats,
    offball_team_rows,
    offball_team_stats,
)
from analytics.sample_game import sample_game_payload


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
    players = offball_player_stats(rundown)
    assert players
    assert players[0].influence_iq >= players[-1].influence_iq
