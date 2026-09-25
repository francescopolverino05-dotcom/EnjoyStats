"""Impact-style 15-stat team sheet counted from match tags."""

from __future__ import annotations

from pathlib import Path

from analytics.game_ingest import collect_game
from analytics.sample_game import sample_game_payload
from analytics.team_sheet import (
    highlight_moments_from_rundown,
    tag_inventory_rows,
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
    inventory = tag_inventory_rows(rundown)
    labels = [row["Tag"] for row in inventory]
    assert "Passes" in labels
    assert "Shots" in labels
    assert "Corners" in labels
    by_tag = {row["Tag"]: row for row in inventory}
    pass_total = sum(
        int(by_tag[label]["Total"])
        for label in ("Passes", "Crosses", "Cutbacks", "Assists")
        if label in by_tag
    )
    assert pass_total == rundown.summary.passes
    assert int(by_tag["Shots"]["Total"]) + int(by_tag["Goals"]["Total"]) == rundown.summary.shots
    assert sum(int(row["Total"]) for row in inventory) == rundown.summary.event_count


def test_arsenal_palace_xml_fills_impact_team_board() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "arsenal_v_palace_1-1.xml"
    rundown = collect_from_film_path(fixture)
    sheets = team_sheets_from_rundown(rundown)
    assert len(sheets) == 2
    home = sheets[0]
    away = sheets[1]
    assert home.team_name == "Arsenal"
    assert away.team_name == "Palace"
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
