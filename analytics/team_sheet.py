"""Impact-style team sheet counted from the same match tags as the rundown.

The 15 basic columns follow Impact Soccer's published team statistics
(goals, assists, possession, shots, shots on target, saves, offsides,
passes, pass accuracy, key passes, duels, fouls, corners, free kicks,
penalties). Every number is a fold of :class:`~data_models.events.MatchEvent`
rows — the Wyscout XML and the film tagger write the same event types.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from pydantic import Field

from analytics.game_ingest import MatchRundown
from data_models.events import EventType, MatchEvent, ShotOutcome
from data_models.player_stats import StrictModel

HIGHLIGHT_HALF_WINDOW_MS = 7_500
PASS_TYPES = {EventType.PASS, EventType.CROSS, EventType.CUTBACK, EventType.ASSIST}
SHOT_TYPES = {EventType.SHOT, EventType.GOAL}
DUEL_TYPES = {EventType.AERIAL_DUEL, EventType.GROUND_DUEL}
HIGHLIGHT_TYPES = {
    EventType.GOAL,
    EventType.SHOT,
    EventType.ASSIST,
    EventType.SAVE,
    EventType.CORNER,
}

# The actions an analyst would otherwise click one-by-one on a tagger.
ANALYST_TAG_ORDER: tuple[EventType, ...] = (
    EventType.GOAL,
    EventType.SHOT,
    EventType.PASS,
    EventType.CROSS,
    EventType.CUTBACK,
    EventType.ASSIST,
    EventType.CORNER,
    EventType.THROW_IN,
    EventType.FREE_KICK,
    EventType.SAVE,
    EventType.INTERCEPTION,
    EventType.BALL_RECOVERY,
    EventType.BALL_LOST,
    EventType.AERIAL_DUEL,
    EventType.GROUND_DUEL,
    EventType.FOUL_COMMITTED,
    EventType.FOUL_WON,
    EventType.OFFSIDE,
    EventType.BLOCK_SHOT,
    EventType.BLOCK_CROSS,
    EventType.BLOCK_PASS,
    EventType.YELLOW_CARD,
    EventType.RED_CARD,
    EventType.GOAL_CONCEDED,
)
ANALYST_TAG_LABELS: dict[EventType, str] = {
    EventType.GOAL: "Goals",
    EventType.SHOT: "Shots",
    EventType.PASS: "Passes",
    EventType.CROSS: "Crosses",
    EventType.CUTBACK: "Cutbacks",
    EventType.ASSIST: "Assists",
    EventType.CORNER: "Corners",
    EventType.THROW_IN: "Throw-ins",
    EventType.FREE_KICK: "Free kicks",
    EventType.SAVE: "Saves",
    EventType.INTERCEPTION: "Interceptions",
    EventType.BALL_RECOVERY: "Recoveries",
    EventType.BALL_LOST: "Balls lost",
    EventType.AERIAL_DUEL: "Aerial duels",
    EventType.GROUND_DUEL: "Ground duels",
    EventType.FOUL_COMMITTED: "Fouls",
    EventType.FOUL_WON: "Fouls won",
    EventType.OFFSIDE: "Offsides",
    EventType.BLOCK_SHOT: "Blocked shots",
    EventType.BLOCK_CROSS: "Blocked crosses",
    EventType.BLOCK_PASS: "Blocked passes",
    EventType.YELLOW_CARD: "Yellow cards",
    EventType.RED_CARD: "Red cards",
    EventType.GOAL_CONCEDED: "Goals conceded",
}
CORE_ANALYST_TAGS: frozenset[EventType] = frozenset(
    {
        EventType.GOAL,
        EventType.SHOT,
        EventType.PASS,
        EventType.CORNER,
        EventType.THROW_IN,
        EventType.FREE_KICK,
        EventType.SAVE,
    }
)


class TeamBasicStats(StrictModel):
    """One team's 15-stat Impact-style headline board."""

    team_id: UUID
    team_name: str = Field(min_length=1, max_length=80)
    goals: int = Field(ge=0)
    assists: int = Field(ge=0)
    possession_pct: float = Field(ge=0.0, le=100.0)
    total_shots: int = Field(ge=0)
    shots_on_target: int = Field(ge=0)
    saves: int = Field(ge=0)
    offsides: int = Field(ge=0)
    total_passes: int = Field(ge=0)
    pass_accuracy: float = Field(ge=0.0, le=100.0)
    key_passes: int = Field(ge=0)
    duels: int = Field(ge=0)
    fouls: int = Field(ge=0)
    corners: int = Field(ge=0)
    free_kicks: int = Field(ge=0)
    penalties: int = Field(ge=0)


class HighlightMoment(StrictModel):
    """A 15-second window around a tagged on-ball moment."""

    clock: str = Field(max_length=24)
    kind: str = Field(max_length=32)
    player: str = Field(max_length=80)
    team_name: str = Field(max_length=80)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


def _team_name(rundown: MatchRundown, team_id: UUID, *, fallback: str) -> str:
    if fallback and fallback not in {"Home", "Away"}:
        return fallback
    members = [row for row in rundown.players if row.team_id == team_id]
    counts: dict[str, int] = defaultdict(int)
    for member in members:
        parts = (member.player_name or "").split()
        if len(parts) >= 2 and len(parts[0]) >= 3 and parts[0][1:2] != ".":
            counts[parts[0]] += 1
    if counts:
        return max(counts, key=counts.get)
    return fallback


def _is_key_pass(event: MatchEvent) -> bool:
    if event.event_type is EventType.ASSIST:
        return True
    if event.event_type not in {EventType.PASS, EventType.CROSS, EventType.CUTBACK}:
        return False
    if event.is_progressive:
        return True
    if event.end_x is None:
        return False
    attacking_right = event.attacking_left_to_right
    return (event.end_x >= 82.0) if attacking_right else (event.end_x <= 18.0)


def team_sheets_from_rundown(rundown: MatchRundown) -> list[TeamBasicStats]:
    """Fold the tag sheet into one 15-stat row per team."""

    events = rundown.events
    team_ids = []
    seen: set[UUID] = set()
    for profile in rundown.players:
        if profile.team_id not in seen:
            seen.add(profile.team_id)
            team_ids.append(profile.team_id)
    team_ids.sort(key=lambda team_id: -sum(1 for row in rundown.players if row.team_id == team_id))
    labels = (
        rundown.summary.home_team_name or "Home",
        rundown.summary.away_team_name or "Away",
    )
    total = max(len(events), 1)
    sheets: list[TeamBasicStats] = []
    for index, team_id in enumerate(team_ids):
        owned = [event for event in events if event.team_id == team_id]
        passes = [event for event in owned if event.event_type in PASS_TYPES]
        shots = [event for event in owned if event.event_type in SHOT_TYPES]
        on_target = [
            event
            for event in shots
            if event.shot_outcome is ShotOutcome.ON_TARGET or event.is_goal
        ]
        successful_passes = sum(1 for event in passes if event.successful)
        sheets.append(
            TeamBasicStats(
                team_id=team_id,
                team_name=_team_name(
                    rundown, team_id, fallback=labels[index] if index < 2 else f"Team {index + 1}"
                ),
                goals=sum(1 for event in owned if event.is_goal or event.event_type is EventType.GOAL),
                assists=sum(1 for event in owned if event.event_type is EventType.ASSIST),
                possession_pct=round(100.0 * len(owned) / total, 1),
                total_shots=len(shots),
                shots_on_target=len(on_target),
                saves=sum(1 for event in owned if event.event_type is EventType.SAVE),
                offsides=sum(1 for event in owned if event.event_type is EventType.OFFSIDE),
                total_passes=len(passes),
                pass_accuracy=round(100.0 * successful_passes / len(passes), 1) if passes else 0.0,
                key_passes=sum(1 for event in owned if _is_key_pass(event)),
                duels=sum(1 for event in owned if event.event_type in DUEL_TYPES),
                fouls=sum(1 for event in owned if event.event_type is EventType.FOUL_COMMITTED),
                corners=sum(1 for event in owned if event.event_type is EventType.CORNER),
                free_kicks=sum(1 for event in owned if event.event_type is EventType.FREE_KICK),
                penalties=sum(1 for event in owned if event.is_penalty),
            )
        )
    return sheets


def highlight_moments_from_rundown(rundown: MatchRundown) -> list[HighlightMoment]:
    """Build 15-second highlight windows around goals, shots, and set pieces."""

    names = {profile.player_id: profile.player_name or "" for profile in rundown.players}
    team_names = {sheet.team_id: sheet.team_name for sheet in team_sheets_from_rundown(rundown)}
    moments: list[HighlightMoment] = []
    for event in rundown.events:
        if event.event_type not in HIGHLIGHT_TYPES and not event.is_goal:
            continue
        start_ms = max(0, event.video_timestamp_ms - HIGHLIGHT_HALF_WINDOW_MS)
        end_ms = event.video_timestamp_ms + HIGHLIGHT_HALF_WINDOW_MS
        moments.append(
            HighlightMoment(
                clock=f"{event.period}' {event.minute:02d}:{event.second:02d}",
                kind="goal" if event.is_goal or event.event_type is EventType.GOAL else event.event_type.value,
                player=names.get(event.player_id, "—") if event.player_id else "—",
                team_name=team_names.get(event.team_id, "Team"),
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
    return moments


def tag_inventory_rows(rundown: MatchRundown) -> list[dict[str, object]]:
    """Count every tag type an analyst would otherwise click by hand.

    Core actions (goals, shots, passes, corners, throw-ins, free kicks,
    saves) always appear so a zero is visible. Other types appear only
    when the sheet actually has them.
    """

    sheets = team_sheets_from_rundown(rundown)
    home_id = sheets[0].team_id if sheets else None
    away_id = sheets[1].team_id if len(sheets) > 1 else None
    home_name = sheets[0].team_name if sheets else (rundown.summary.home_team_name or "Home")
    away_name = (
        sheets[1].team_name if len(sheets) > 1 else (rundown.summary.away_team_name or "Away")
    )
    totals: dict[EventType, int] = defaultdict(int)
    home_counts: dict[EventType, int] = defaultdict(int)
    away_counts: dict[EventType, int] = defaultdict(int)
    for event in rundown.events:
        kind = event.event_type
        totals[kind] += 1
        if home_id is not None and event.team_id == home_id:
            home_counts[kind] += 1
        elif away_id is not None and event.team_id == away_id:
            away_counts[kind] += 1

    rows: list[dict[str, object]] = []
    listed: set[EventType] = set()
    for kind in ANALYST_TAG_ORDER:
        listed.add(kind)
        total = totals[kind]
        if total == 0 and kind not in CORE_ANALYST_TAGS:
            continue
        rows.append(
            {
                "Tag": ANALYST_TAG_LABELS[kind],
                "Total": total,
                home_name: home_counts[kind],
                away_name: away_counts[kind],
            }
        )
    leftovers = sorted(
        (kind for kind in totals if kind not in listed and totals[kind] > 0),
        key=lambda kind: kind.value,
    )
    for kind in leftovers:
        rows.append(
            {
                "Tag": ANALYST_TAG_LABELS.get(kind, kind.value.replace("_", " ").title()),
                "Total": totals[kind],
                home_name: home_counts[kind],
                away_name: away_counts[kind],
            }
        )
    return rows


def team_sheet_rows(sheets: Sequence[TeamBasicStats]) -> list[dict[str, object]]:
    """Transpose team sheets into a metric × team table for the dashboard."""

    if not sheets:
        return []
    fields = (
        ("Goals", "goals"),
        ("Assists", "assists"),
        ("Possession %", "possession_pct"),
        ("Total shots", "total_shots"),
        ("Shots on target", "shots_on_target"),
        ("Saves", "saves"),
        ("Offsides", "offsides"),
        ("Total passes", "total_passes"),
        ("Pass accuracy %", "pass_accuracy"),
        ("Key passes", "key_passes"),
        ("Duels", "duels"),
        ("Fouls", "fouls"),
        ("Corners", "corners"),
        ("Free kicks", "free_kicks"),
        ("Penalties", "penalties"),
    )
    rows: list[dict[str, object]] = []
    for label, attr in fields:
        row: dict[str, object] = {"Stat": label}
        for sheet in sheets:
            row[sheet.team_name] = getattr(sheet, attr)
        rows.append(row)
    return rows
