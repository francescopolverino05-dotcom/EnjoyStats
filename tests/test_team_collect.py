"""Collective Home / Away four-pillar sheets from match tags."""

from __future__ import annotations

from pathlib import Path

from analytics.game_ingest import collect_game
from analytics.match_tags import collect_from_tag_xml
from analytics.sample_game import TEAM_ID, sample_game_payload
from analytics.team_collect import (
    is_invented_film_identity,
    is_one_sided_sheet,
    named_player_profiles,
    team_profiles_from_rundown,
)
from app.ingest import actions_from_team, load_from_team_profile
from data_models.player_stats import PlayerMatchProfile


def test_sample_match_folds_into_one_team_profile() -> None:
    rundown = collect_game(sample_game_payload())
    teams = team_profiles_from_rundown(rundown)
    assert len(teams) == 1
    team = teams[0]
    assert team.player_id == TEAM_ID
    assert team.position == "TEAM"
    assert team.offensive.goals == rundown.summary.goals
    assert team.distribution.passes.total >= rundown.summary.passes
    load = load_from_team_profile(rundown, team)
    assert load.source == "collected"
    assert load.profile.player_id == TEAM_ID
    assert any(action.is_goal for action in load.actions)
    assert actions_from_team(rundown.events, TEAM_ID)


def test_arsenal_palace_xml_has_two_collective_sheets() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "arsenal_v_palace_1-1.xml"
    rundown = collect_from_tag_xml(fixture.read_text(encoding="utf-8-sig"))
    teams = team_profiles_from_rundown(rundown)
    assert len(teams) == 2
    assert all(row.position == "TEAM" for row in teams)
    assert {row.player_name for row in teams} == {"Arsenal", "Palace"}
    assert {row.offensive.goals for row in teams} == {1}
    assert is_one_sided_sheet(rundown) is True
    assert sum(row.distribution.passes.total for row in teams) >= 400
    named = named_player_profiles(rundown)
    assert named
    assert all(not is_invented_film_identity(row) for row in named)
    home = teams[0]
    load = load_from_team_profile(rundown, home)
    assert load.actions
    assert home.player_id not in {row.player_id for row in rundown.players}


def test_invented_film_names_are_hidden() -> None:
    rundown = collect_game(sample_game_payload())
    fake = rundown.players[0].model_copy(update={"player_name": "Home CM 4"})
    assert is_invented_film_identity(fake)
    assert not is_invented_film_identity(rundown.players[0])
    hidden = rundown.model_copy(update={"players": [fake]})
    assert named_player_profiles(hidden) == []


def test_team_profile_is_not_a_rundown_player_row() -> None:
    rundown = collect_game(sample_game_payload())
    team = team_profiles_from_rundown(rundown)[0]
    assert isinstance(team, PlayerMatchProfile)
    assert team.position == "TEAM"
    assert team.offensive.minutes > 0
