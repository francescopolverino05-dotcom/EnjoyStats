"""Step D — edit / delete wrong tags before export (no full hand-tagging)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from analytics.block_coverage import (
    build_coverage_report,
    coverage_as_dicts,
)
from analytics.game_ingest import (
    GamePayload,
    MatchRundown,
    PlayerRosterEntry,
    collect_game,
    event_clock_minutes,
)
from data_models.events import EventType, MatchEvent, ShotOutcome


def player_label_map(rundown: MatchRundown) -> dict[str, UUID]:
    """Display label → player_id for Review dropdowns."""

    labels: dict[str, UUID] = {}
    for profile in rundown.players:
        name = (profile.player_name or "Player").strip() or "Player"
        if profile.jersey_number is not None:
            label = f"({profile.jersey_number}) {name}"
        else:
            label = name
        # Keep first if duplicate labels.
        labels.setdefault(label, profile.player_id)
    return labels


def review_rows(rundown: MatchRundown) -> list[dict[str, Any]]:
    """Rows for ``st.data_editor`` — one row per tag."""

    labels = {pid: label for label, pid in player_label_map(rundown).items()}
    # Invert carefully: player_label_map is label→id; rebuild id→label.
    id_to_label: dict[UUID, str] = {}
    for label, pid in player_label_map(rundown).items():
        id_to_label[pid] = label

    rows: list[dict[str, Any]] = []
    for event in rundown.events:
        actor = id_to_label.get(event.player_id, "—") if event.player_id else "—"
        rows.append(
            {
                "keep": True,
                "event_id": str(event.event_id),
                "clock": f"{event.period}' {event.minute:02d}:{event.second:02d}",
                "tag": event.event_type.value,
                "player": actor,
                "x": round(event.x, 1),
                "y": round(event.y, 1),
                "goal": bool(event.is_goal),
            }
        )
    return rows


def apply_review_edits(
    rundown: MatchRundown,
    edited_rows: Sequence[Mapping[str, Any]],
) -> MatchRundown:
    """Rebuild rundown from Review table edits (delete + type/player changes)."""

    by_id = {event.event_id: event for event in rundown.events}
    labels = player_label_map(rundown)
    kept: list[MatchEvent] = []
    for row in edited_rows:
        if not bool(row.get("keep", True)):
            continue
        raw_id = str(row.get("event_id", "")).strip()
        try:
            event_id = UUID(raw_id)
        except ValueError:
            continue
        original = by_id.get(event_id)
        if original is None:
            continue
        tag_raw = str(row.get("tag", original.event_type.value)).strip().lower()
        try:
            new_type = EventType(tag_raw)
        except ValueError:
            new_type = original.event_type
        player_label = str(row.get("player", "")).strip()
        new_player = labels.get(player_label, original.player_id)
        updates: dict[str, Any] = {"event_type": new_type, "player_id": new_player}
        if new_type is EventType.GOAL:
            updates["is_goal"] = True
            updates["shot_outcome"] = ShotOutcome.ON_TARGET
        elif new_type is EventType.SHOT and original.shot_outcome is None:
            updates["shot_outcome"] = ShotOutcome.MISSED
            updates["is_goal"] = False
        # Pass-like types need end coords.
        if new_type in {
            EventType.PASS,
            EventType.CROSS,
            EventType.CUTBACK,
            EventType.ASSIST,
        }:
            if original.end_x is None:
                updates["end_x"] = min(100.0, original.x + 5.0)
            if original.end_y is None:
                updates["end_y"] = original.y
        kept.append(original.model_copy(update=updates))

    if not kept:
        raise ValueError("Review kept zero tags — tick Keep on at least one row.")

    roster = [
        PlayerRosterEntry(
            player_id=profile.player_id,
            team_id=profile.team_id,
            jersey_number=profile.jersey_number,
            player_name=profile.player_name,
            position=profile.position,
        )
        for profile in rundown.players
    ]
    return collect_game(
        GamePayload(
            match_id=rundown.match_id,
            players=roster,
            events=kept,
            home_team_name=rundown.summary.home_team_name,
            away_team_name=rundown.summary.away_team_name,
            tag_source=rundown.summary.tag_source,
        )
    )


def coverage_rows_for_rundown(rundown: MatchRundown) -> list[dict[str, object]]:
    """5-minute coverage table for the Review / Coverage panel."""

    home_ids = set()
    # First team seen among players that match home name prefix, else first team.
    home_name = (rundown.summary.home_team_name or "Home").strip().lower()
    for profile in rundown.players:
        label = (profile.player_name or "").strip().lower()
        if label.startswith(home_name):
            home_ids.add(profile.team_id)
    if not home_ids and rundown.players:
        home_ids.add(rundown.players[0].team_id)
    duration = max(rundown.summary.duration_minutes, 0.1)
    if rundown.events:
        duration = max(duration, max(event_clock_minutes(e) for e in rundown.events))
    coverage = build_coverage_report(
        rundown.events,
        duration,
        home_team_ids=home_ids,
    )
    return coverage_as_dicts(coverage)


def event_type_choices() -> list[str]:
    """Selectable tag types in Review."""

    return [item.value for item in EventType]
