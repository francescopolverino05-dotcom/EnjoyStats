"""Turn a tagged match feed into collected player-match rundowns.

Upload a JSON event export (or the bundled sample game). Every event is
folded through :class:`~analytics.stats_collector.PlayerStatsCollector`
so the dashboard can present the full four-pillar stat sheet without
hand-entering totals.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from analytics.stats_collector import PlayerStatsCollector, new_collector
from data_models.events import EventType, MatchEvent
from data_models.player_stats import PlayerMatchProfile, PossessionStats, StrictModel


class PlayerRosterEntry(StrictModel):
    """Identity for a player appearing in an uploaded match."""

    player_id: UUID
    team_id: UUID
    jersey_number: int | None = Field(default=None, ge=1, le=99)
    player_name: str = Field(default="", max_length=80)
    position: str = Field(default="", max_length=8)

    @field_validator("position")
    @classmethod
    def normalize_position(cls, value: str) -> str:
        """Upper-case compact position codes."""

        return value.strip().upper()


class GamePayload(StrictModel):
    """Accepted upload document: roster plus tagged AutoData events."""

    match_id: UUID | None = None
    players: list[PlayerRosterEntry] = Field(default_factory=list)
    events: list[MatchEvent] = Field(min_length=1)
    home_team_name: str = Field(default="Home", max_length=80)
    away_team_name: str = Field(default="Away", max_length=80)


class MatchSummary(StrictModel):
    """Match-level headline numbers shown above the player rundown."""

    match_id: UUID
    event_count: int = Field(ge=0)
    player_count: int = Field(ge=0)
    goals: int = Field(ge=0)
    shots: int = Field(ge=0)
    passes: int = Field(ge=0)
    duration_minutes: float = Field(ge=0.0)
    home_team_name: str = Field(default="Home", max_length=80)
    away_team_name: str = Field(default="Away", max_length=80)


class MatchRundown(StrictModel):
    """Collected profiles plus the event feed they were built from."""

    match_id: UUID
    summary: MatchSummary
    players: list[PlayerMatchProfile]
    events: list[MatchEvent]


def event_clock_minutes(event: MatchEvent) -> float:
    """Absolute match clock in minutes (45-minute halves)."""

    return (event.period - 1) * 45.0 + float(event.minute) + (event.second / 60.0)


def parse_game_payload(raw: object) -> GamePayload:
    """Parse a JSON array of events or an object with ``events``.

    Args:
        raw: Decoded JSON document.

    Returns:
        A validated :class:`GamePayload`.

    Raises:
        ValueError: If the document is empty or the wrong JSON shape.
    """

    if isinstance(raw, list):
        if not raw:
            raise ValueError("Game file has no events to collect.")
        return GamePayload(events=[MatchEvent.model_validate(item) for item in raw])
    if isinstance(raw, dict):
        return GamePayload.model_validate(raw)
    raise ValueError("Game file must be a JSON array of events or an object with events.")


def _roster_for_events(
    events: Sequence[MatchEvent],
    declared: Sequence[PlayerRosterEntry],
) -> dict[UUID, PlayerRosterEntry]:
    """Merge an optional roster with identities inferred from the feed."""

    roster: dict[UUID, PlayerRosterEntry] = {entry.player_id: entry for entry in declared}
    for event in events:
        if event.player_id is None:
            continue
        existing = roster.get(event.player_id)
        if existing is None:
            roster[event.player_id] = PlayerRosterEntry(
                player_id=event.player_id,
                team_id=event.team_id,
            )
            continue
        if existing.team_id != event.team_id:
            raise ValueError(
                f"Player {event.player_id} appears for more than one team in this match."
            )
    if not roster:
        raise ValueError("Game has no player-tagged events to collect.")
    return roster


def _apply_possession(
    stats: PlayerMatchProfile,
    *,
    player_events: int,
    total_events: int,
    match_minutes: float,
) -> PlayerMatchProfile:
    """Fill possession from the player's share of the tagged feed."""

    if total_events <= 0 or match_minutes <= 0:
        return stats
    share = player_events / total_events
    possession = PossessionStats(
        time_minutes=min(match_minutes, share * match_minutes),
        percentage=share * 100.0,
    )
    updated = stats.replace_pillars(possession=possession)
    return PlayerMatchProfile.from_stats(updated)


def collect_game(payload: GamePayload) -> MatchRundown:
    """Fold every tagged event into per-player four-pillar profiles.

    Args:
        payload: Uploaded match document.

    Returns:
        Collected profiles ordered by jersey number then name.

    Raises:
        ValueError: If events span more than one match or have no actors.
    """

    match_ids = {event.match_id for event in payload.events}
    if payload.match_id is not None:
        match_ids.add(payload.match_id)
    if len(match_ids) != 1:
        raise ValueError("All events must belong to a single match_id.")
    match_id = next(iter(match_ids))

    ordered = sorted(
        payload.events,
        key=lambda event: (event.period, event.minute, event.second, str(event.event_id)),
    )
    roster = _roster_for_events(ordered, payload.players)
    collectors: dict[UUID, PlayerStatsCollector] = {
        player_id: new_collector(
            match_id=match_id,
            player_id=entry.player_id,
            team_id=entry.team_id,
            jersey_number=entry.jersey_number,
            position=entry.position,
            player_name=entry.player_name,
        )
        for player_id, entry in roster.items()
    }

    event_counts: dict[UUID, int] = defaultdict(int)
    for event in ordered:
        if event.match_id != match_id:
            raise ValueError("All events must belong to a single match_id.")
        if event.player_id is None:
            continue
        collector = collectors.get(event.player_id)
        if collector is None:
            continue
        collector.apply(event)
        event_counts[event.player_id] += 1

    match_minutes = max((event_clock_minutes(event) for event in ordered), default=0.0)
    if match_minutes <= 0:
        match_minutes = 0.1
    tagged_total = max(sum(event_counts.values()), 1)

    profiles: list[PlayerMatchProfile] = []
    for player_id, collector in collectors.items():
        profile = PlayerMatchProfile.from_stats(collector.stats)
        profiles.append(
            _apply_possession(
                profile,
                player_events=event_counts[player_id],
                total_events=tagged_total,
                match_minutes=match_minutes,
            )
        )
    profiles.sort(
        key=lambda row: (
            row.jersey_number is None,
            row.jersey_number or 99,
            row.player_name,
            str(row.player_id),
        )
    )

    shot_types = {EventType.SHOT, EventType.GOAL}
    pass_types = {EventType.PASS, EventType.CROSS, EventType.CUTBACK, EventType.ASSIST}
    summary = MatchSummary(
        match_id=match_id,
        event_count=len(ordered),
        player_count=len(profiles),
        goals=sum(1 for event in ordered if event.is_goal or event.event_type is EventType.GOAL),
        shots=sum(1 for event in ordered if event.event_type in shot_types),
        passes=sum(1 for event in ordered if event.event_type in pass_types),
        duration_minutes=round(match_minutes, 1),
        home_team_name=payload.home_team_name or "Home",
        away_team_name=payload.away_team_name or "Away",
    )
    return MatchRundown(
        match_id=match_id,
        summary=summary,
        players=profiles,
        events=ordered,
    )


def parse_and_collect(raw: object) -> MatchRundown:
    """Parse uploaded JSON and collect the match rundown in one step."""

    return collect_game(parse_game_payload(raw))


def rundown_to_json(rundown: MatchRundown) -> dict[str, Any]:
    """Serialize a rundown for Streamlit session state.

    Computed fields (``success_rate``, ``shot_accuracy``, ``total`` on nested
    splits) are omitted so StrictModel rehydration does not fail with
    ``extra="forbid"``.
    """

    return rundown.model_dump(mode="json", exclude_computed_fields=True)


def rundown_from_mapping(payload: Mapping[str, Any]) -> MatchRundown:
    """Rehydrate a rundown stored in session state."""

    return MatchRundown.model_validate(dict(payload))
