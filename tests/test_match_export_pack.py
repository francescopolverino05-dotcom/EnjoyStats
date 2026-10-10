"""Excel / highlights / share-pack exports."""

from __future__ import annotations

import zipfile
from io import BytesIO

from analytics.game_ingest import collect_game
from analytics.match_export_pack import (
    build_match_excel,
    build_share_pack,
    highlights_to_csv,
)
from analytics.sample_game import sample_game_payload


def test_excel_workbook_is_valid_zip_with_sheets() -> None:
    rundown = collect_game(sample_game_payload())
    data = build_match_excel(rundown)
    assert data[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/worksheets/sheet5.xml" in names


def test_highlights_csv_and_share_pack() -> None:
    rundown = collect_game(sample_game_payload())
    csv_text = highlights_to_csv(rundown)
    assert "Clock" in csv_text
    assert "Start ms" in csv_text
    pack = build_share_pack(rundown)
    with zipfile.ZipFile(BytesIO(pack)) as archive:
        listing = archive.namelist()
        assert any(name.endswith("_stats.xlsx") for name in listing)
        assert any(name.endswith("_highlights.csv") for name in listing)
        assert any(name.endswith("_tags.csv") for name in listing)
        assert any(name.endswith("_offball_iq.csv") for name in listing)
