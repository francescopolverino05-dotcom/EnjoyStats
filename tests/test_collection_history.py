"""Tests for auto-saved Analyse collection history and PDF reports."""

from __future__ import annotations

from pathlib import Path

from analytics.collection_history import (
    delete_history_entry,
    list_history,
    load_history_rundown,
    save_rundown_to_history,
)
from analytics.game_ingest import collect_game
from analytics.match_report_pdf import build_match_report_pdf
from analytics.sample_game import sample_game_payload


def test_save_list_load_and_update_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    rundown = collect_game(sample_game_payload())
    first = save_rundown_to_history(rundown)
    assert first.label
    assert "Napoleon" in first.label or "Jeans" in first.label
    assert first.event_count == rundown.summary.event_count
    assert (tmp_path / "history" / first.rundown_file).is_file()

    listed = list_history()
    assert len(listed) == 1
    assert listed[0].history_id == first.history_id

    loaded = load_history_rundown(first.history_id)
    assert loaded is not None
    assert loaded.match_id == rundown.match_id
    assert loaded.summary.event_count == rundown.summary.event_count

    again = save_rundown_to_history(rundown)
    assert again.history_id == first.history_id
    assert len(list_history()) == 1


def test_delete_history_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    rundown = collect_game(sample_game_payload())
    entry = save_rundown_to_history(rundown)
    path = Path(tmp_path / "history" / entry.rundown_file)
    assert path.is_file()
    assert delete_history_entry(entry.history_id) is True
    assert list_history() == []
    assert not path.exists()
    assert delete_history_entry(entry.history_id) is False


def test_match_report_pdf_has_cover_and_tables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    rundown = collect_game(sample_game_payload())
    entry = save_rundown_to_history(rundown)
    pdf = build_match_report_pdf(rundown, history=entry)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 2_000
