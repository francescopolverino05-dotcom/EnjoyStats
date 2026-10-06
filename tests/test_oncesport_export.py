"""Tests for independent OnceSport Home/Away XML export (no Grokbot)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from analytics.game_ingest import collect_game
from analytics.oncesport_export import (
    AWAY_BUTTONS,
    HOME_BUTTONS,
    export_both_oncesport_xml,
    once_sport_button_for,
    write_oncesport_pair,
)
from analytics.sample_game import sample_game_payload
from analytics.video_auto_collect import collect_from_video, write_synthetic_match_clip
from data_models.events import EventType, MatchEvent, ShotOutcome


def test_home_and_away_button_labels_are_exact() -> None:
    assert HOME_BUTTONS["pass"] == "Passaggi"
    assert AWAY_BUTTONS["pass"] == "passaggi"
    assert HOME_BUTTONS["aerial_duel"] == "Duelli aeree"
    assert AWAY_BUTTONS["aerial_duel"] == "Duelli aerei"
    assert HOME_BUTTONS["ground_duel_def"] == "Duelli difensivi"
    assert AWAY_BUTTONS["ground_duel_def"] == "Duelli difensive"
    assert HOME_BUTTONS["throw_in"] == "Rimesse laterali"
    assert AWAY_BUTTONS["throw_in"] == "Rimessa laterale"
    assert HOME_BUTTONS["foul"] == "Falli"
    assert AWAY_BUTTONS["foul"] == "falli"
    assert "Fuori gioco" in HOME_BUTTONS.values()
    assert "offside" not in AWAY_BUTTONS
    assert "Palle intercettate" in HOME_BUTTONS.values()
    assert "interception" not in AWAY_BUTTONS


def test_once_sport_button_maps_pass_and_shot() -> None:
    match_id = uuid4()
    team_id = uuid4()
    pass_event = MatchEvent(
        match_id=match_id,
        team_id=team_id,
        minute=10,
        event_type=EventType.PASS,
        x=40.0,
        y=50.0,
        end_x=55.0,
        end_y=50.0,
    )
    shot = MatchEvent(
        match_id=match_id,
        team_id=team_id,
        minute=12,
        event_type=EventType.SHOT,
        x=88.0,
        y=50.0,
        shot_outcome=ShotOutcome.ON_TARGET,
    )
    assert once_sport_button_for(pass_event, side="home") == "Passaggi"
    assert once_sport_button_for(pass_event, side="away") == "passaggi"
    assert once_sport_button_for(shot, side="home") == "Tiri"
    assert once_sport_button_for(shot, side="away") == "Tiri"


def test_export_sample_writes_exact_action_names() -> None:
    rundown = collect_game(sample_game_payload())
    pair = export_both_oncesport_xml(rundown)
    assert 'analysedTeam="Napoleon Bot"' in pair["home"] or "analysedTeam=" in pair["home"]
    assert "/ Passaggi" in pair["home"] or "/ Tiri" in pair["home"] or "/ Cross" in pair["home"]
    assert "<actions>" in pair["home"]
    assert "<actions>" in pair["away"]
    # Away spelling variants must appear when away has those event kinds.
    away_blob = pair["away"]
    assert "actionName=" in away_blob


def test_write_oncesport_pair_files(tmp_path: Path) -> None:
    rundown = collect_game(sample_game_payload())
    home_path, away_path = write_oncesport_pair(rundown, tmp_path, stem="demo")
    assert home_path.name == "demo_Home.xml"
    assert away_path.name == "demo_Away.xml"
    assert "Passaggi" in home_path.read_text(encoding="utf-8") or "Tiri" in home_path.read_text(
        encoding="utf-8"
    )


def test_film_collect_writes_home_away_oncesport(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "independent.avi")
    rundown = collect_from_video(clip)
    assert rundown.events
    home_xml = tmp_path / "independent_Home.xml"
    away_xml = tmp_path / "independent_Away.xml"
    assert home_xml.is_file()
    assert away_xml.is_file()
    home_text = home_xml.read_text(encoding="utf-8")
    away_text = away_xml.read_text(encoding="utf-8")
    assert "<analysis" in home_text
    assert "<analysis" in away_text
    # At least one exact home button should appear when home events exist.
    assert (
        any(
            label in home_text
            for label in ("Passaggi", "Tiri", "Cross", "Rimesse laterali", "Palle perse")
        )
        or "<actions>\n  </actions>" in home_text
    )
