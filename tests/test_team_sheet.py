"""Impact-style 15-stat team sheet counted from match tags."""

from __future__ import annotations

from pathlib import Path

from analytics.game_ingest import collect_game
from analytics.match_tags import collect_from_tag_xml
from analytics.sample_game import sample_game_payload
from analytics.team_sheet import (
    highlight_moments_from_rundown,
    team_sheet_rows,
    team_sheets_from_rundown,
)
from app.ingest import collect_from_film_path, film_has_official_tags


def test_sample_game_team_sheet_has_fifteen_stats() -> None:
    rundown = collect_game(sample_game_payload())
    sheets = team_sheets_from_rundown(rundown)
    assert sheets
    rows = team_sheet_rows(sheets)
    labels = [row["Stat"] for row in rows]
    assert labels == [
        "Goals",
        "Assists",
        "Possession %",
        "Total shots",
        "Shots on target",
        "Saves",
        "Offsides",
        "Total passes",
        "Pass accuracy %",
        "Key passes",
        "Duels",
        "Fouls",
        "Corners",
        "Free kicks",
        "Penalties",
    ]
    assert sum(sheet.goals for sheet in sheets) == rundown.summary.goals


def test_arsenal_palace_xml_fills_impact_team_board() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "arsenal_v_palace_1-1.xml"
    rundown = collect_from_film_path(fixture)
    sheets = team_sheets_from_rundown(rundown)
    assert len(sheets) == 2
    by_name = {sheet.team_name: sheet for sheet in sheets}
    home = sheets[0]
    away = sheets[1]
    assert home.goals == 1
    assert away.goals == 1
    assert home.total_passes >= 300
    assert home.saves == 2
    assert home.corners >= 1
    assert home.free_kicks >= 1
    assert home.duels >= 10
    assert abs(home.possession_pct + away.possession_pct - 100.0) < 0.2
    moments = highlight_moments_from_rundown(rundown)
    kinds = {moment.kind for moment in moments}
    assert "goal" in kinds
    assert any(moment.end_ms - moment.start_ms == 15_000 for moment in moments)


def test_film_with_sibling_xml_is_immediate(tmp_path: Path) -> None:
    clip = tmp_path / "Arsenal_v_Palace.mp4"
    clip.write_bytes(b"x")
    xml = tmp_path / "Arsenal_v_Palace.xml"
    xml.write_text(
        (Path(__file__).resolve().parent / "fixtures" / "arsenal_v_palace_1-1.xml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    assert film_has_official_tags(xml)
    assert film_has_official_tags(clip)
