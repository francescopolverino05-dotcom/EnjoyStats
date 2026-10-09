"""Persist collected match rundowns so Analyse History survives restarts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from analytics.game_ingest import MatchRundown, rundown_from_mapping, rundown_to_json
from analytics.team_collect import analysis_perspective
from analytics.team_sheet import team_sheets_from_rundown
from data_models.player_stats import StrictModel
from pydantic import Field


def history_dir() -> Path:
    """Directory for saved collection history (override with ENJOYSTATS_HISTORY_DIR)."""

    override = os.environ.get("ENJOYSTATS_HISTORY_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        path = Path.cwd() / ".local-run" / "history"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _index_path() -> Path:
    return history_dir() / "index.json"


class HistoryEntry(StrictModel):
    """One saved collect in the Analyse history list."""

    history_id: str = Field(min_length=8, max_length=64)
    saved_at: str = Field(min_length=8, max_length=64)
    match_id: str = Field(min_length=8, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    home_team_name: str = Field(default="Home", max_length=80)
    away_team_name: str = Field(default="Away", max_length=80)
    home_goals: int = Field(ge=0)
    away_goals: int = Field(ge=0)
    event_count: int = Field(ge=0)
    player_count: int = Field(ge=0)
    tag_source: str = Field(default="official", max_length=32)
    rundown_file: str = Field(min_length=1, max_length=200)


def _load_index() -> list[HistoryEntry]:
    path = _index_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    entries: list[HistoryEntry] = []
    for item in raw:
        try:
            entries.append(HistoryEntry.model_validate(item))
        except Exception:  # noqa: BLE001 — skip corrupt rows
            continue
    entries.sort(key=lambda row: row.saved_at, reverse=True)
    return entries


def _write_index(entries: list[HistoryEntry]) -> None:
    payload = [entry.model_dump(mode="json") for entry in entries]
    _index_path().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _score_for_rundown(rundown: MatchRundown) -> tuple[str, str, int, int]:
    sheets = team_sheets_from_rundown(rundown)
    home = rundown.summary.home_team_name or "Home"
    away = rundown.summary.away_team_name or "Away"
    official_h = getattr(rundown.summary, "official_home_goals", None)
    official_a = getattr(rundown.summary, "official_away_goals", None)
    if official_h is not None and official_a is not None:
        return home, away, int(official_h), int(official_a)
    if len(sheets) >= 2:
        # Sheets are home-then-away; keep summary labels when they are real clubs.
        home_name = home if home not in {"Home", "Away"} else sheets[0].team_name
        away_name = away if away not in {"Home", "Away"} else sheets[1].team_name
        return home_name, away_name, sheets[0].goals, sheets[1].goals
    if len(sheets) == 1:
        perspective = analysis_perspective(rundown)
        return (
            perspective.analysed_team_name,
            perspective.opposition_team_name,
            perspective.analysed_goals,
            perspective.opposition_goals_on_sheet,
        )
    return home, away, 0, 0


def save_rundown_to_history(rundown: MatchRundown) -> HistoryEntry:
    """Write the rundown to disk and prepend it to the history index.

    Re-saving the same ``match_id`` updates that history row in place so the
    sample / recollect flow does not flood the list.
    """

    home, away, home_goals, away_goals = _score_for_rundown(rundown)
    label = f"{home} {home_goals}–{away_goals} {away}"
    entries = _load_index()
    existing = next((row for row in entries if row.match_id == str(rundown.match_id)), None)
    history_id = existing.history_id if existing is not None else uuid4().hex[:12]
    rundown_name = f"{history_id}.rundown.json"
    rundown_path = history_dir() / rundown_name
    rundown_path.write_text(
        json.dumps(rundown_to_json(rundown), indent=2) + "\n",
        encoding="utf-8",
    )
    entry = HistoryEntry(
        history_id=history_id,
        saved_at=datetime.now(timezone.utc).isoformat(),
        match_id=str(rundown.match_id),
        label=label,
        home_team_name=home,
        away_team_name=away,
        home_goals=home_goals,
        away_goals=away_goals,
        event_count=rundown.summary.event_count,
        player_count=rundown.summary.player_count,
        tag_source=getattr(rundown.summary, "tag_source", "official") or "official",
        rundown_file=rundown_name,
    )
    entries = [row for row in entries if row.history_id != history_id]
    entries.insert(0, entry)
    _write_index(entries)
    return entry


def list_history() -> list[HistoryEntry]:
    """Newest-first history rows."""

    return _load_index()


def load_history_rundown(history_id: str) -> MatchRundown | None:
    """Load a saved rundown by history id."""

    entry = next((row for row in _load_index() if row.history_id == history_id), None)
    if entry is None:
        return None
    path = history_dir() / entry.rundown_file
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return rundown_from_mapping(payload)


def delete_history_entry(history_id: str) -> bool:
    """Remove one history row and its rundown file."""

    entries = _load_index()
    entry = next((row for row in entries if row.history_id == history_id), None)
    if entry is None:
        return False
    path = history_dir() / entry.rundown_file
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    _write_index([row for row in entries if row.history_id != history_id])
    return True
