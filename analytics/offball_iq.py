"""Off-ball / StatMan IQ proxies counted from the match tag sheet.

Impact Soccer publishes off-the-ball aggregates (halos, triangles, compact /
stretched defence, alleys, recovery, pressing). We do not have full 22-player
tracking on every frame, so these metrics are **event-derived proxies** that
approximate the same ideas from passes, recoveries, interceptions, and duels.

Set pieces (corners, free kicks, throw-ins) are a high-signal IQ lever —
roughly 10–25% of goals in elite football. Public models (StatsBomb phases,
Hudl IQ corners, wa-setpieces) split value into:

* **Phase 1** — delivery + first contact (aerial / recovery / block)
* **Phase 2** — second-ball shots and retained pressure after the restart

Scores are 0–100 team/player Influence IQ figures for the Match rundown.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import Field

from analytics.game_ingest import MatchRundown
from analytics.team_sheet import team_sheets_from_rundown
from data_models.events import EventType, MatchEvent
from data_models.player_stats import StrictModel

PASS_TYPES = {EventType.PASS, EventType.CROSS, EventType.CUTBACK, EventType.ASSIST}
DEF_ACTIONS = {
    EventType.BALL_RECOVERY,
    EventType.INTERCEPTION,
    EventType.BLOCK_SHOT,
    EventType.BLOCK_CROSS,
    EventType.BLOCK_PASS,
    EventType.GROUND_DUEL,
    EventType.AERIAL_DUEL,
}
SET_PIECE_TYPES = {EventType.CORNER, EventType.FREE_KICK, EventType.THROW_IN}
FIRST_CONTACT_TYPES = {
    EventType.AERIAL_DUEL,
    EventType.BALL_RECOVERY,
    EventType.INTERCEPTION,
    EventType.BLOCK_SHOT,
    EventType.BLOCK_CROSS,
    EventType.BLOCK_PASS,
    EventType.GROUND_DUEL,
}
PHASE2_SHOT_TYPES = {EventType.SHOT, EventType.GOAL}
# StatsBomb-style windows: first contact ~8–12s; second phase out to ~30s.
PHASE1_WINDOW_S = 12.0
PHASE2_WINDOW_S = 30.0


class OffBallTeamStats(StrictModel):
    """Impact-style off-ball board for one side."""

    team_id: UUID
    team_name: str = Field(min_length=1, max_length=80)
    player_halos: int = Field(ge=0)
    attacking_triangles: int = Field(ge=0)
    defensive_triangles: int = Field(ge=0)
    compact_defence: float = Field(ge=0.0, le=100.0)
    stretched_defence: float = Field(ge=0.0, le=100.0)
    uncontested_alleys: int = Field(ge=0)
    defensive_recoveries: int = Field(ge=0)
    pressing_ability: float = Field(ge=0.0, le=100.0)
    set_piece_deliveries: int = Field(ge=0)
    set_piece_first_contact: int = Field(ge=0)
    set_piece_second_phase: int = Field(ge=0)
    influence_iq: float = Field(ge=0.0, le=100.0)


class OffBallPlayerStats(StrictModel):
    """Per-player off-ball influence proxies."""

    player_id: UUID
    team_id: UUID
    player_name: str = Field(min_length=1, max_length=80)
    jersey_number: int | None = None
    position: str = Field(default="", max_length=16)
    player_halo: int = Field(ge=0)
    attacking_triangles: int = Field(ge=0)
    defensive_recoveries: int = Field(ge=0)
    uncontested_alleys: int = Field(ge=0)
    pressing_actions: int = Field(ge=0)
    set_piece_deliveries: int = Field(ge=0)
    set_piece_first_contact: int = Field(ge=0)
    set_piece_second_phase: int = Field(ge=0)
    influence_iq: float = Field(ge=0.0, le=100.0)


@dataclass(frozen=True)
class _SetPieceTally:
    deliveries: int = 0
    first_contact: int = 0
    second_phase: int = 0


def _clock_s(event: MatchEvent) -> float:
    return float(event.period - 1) * 45.0 * 60.0 + event.minute * 60.0 + event.second


def _third(x: float, *, attacking_right: bool) -> str:
    if attacking_right:
        if x < 33.3:
            return "defensive"
        if x < 66.7:
            return "middle"
        return "final"
    if x > 66.7:
        return "defensive"
    if x > 33.3:
        return "middle"
    return "final"


def _is_half_space(y: float) -> bool:
    return (20.0 <= y < 40.0) or (60.0 < y <= 80.0)


def _count_pass_triangles(
    events: Sequence[MatchEvent],
    *,
    team_id: UUID,
) -> int:
    """Count short 3-pass chains with non-collinear geometry (triangle shape)."""

    passes = [
        event
        for event in events
        if event.team_id == team_id
        and event.event_type in PASS_TYPES
        and event.successful
        and event.end_x is not None
        and event.end_y is not None
    ]
    triangles = 0
    for index in range(len(passes) - 2):
        a, b, c = passes[index], passes[index + 1], passes[index + 2]
        if _clock_s(c) - _clock_s(a) > 18.0:
            continue
        # Points: start A → end A/start B → end C
        x1, y1 = a.x, a.y
        x2, y2 = float(a.end_x), float(a.end_y)
        x3, y3 = float(c.end_x or c.x), float(c.end_y or c.y)
        area = abs((x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)) / 2.0)
        if area >= 40.0:  # non-trivial triangle on 0–100 pitch
            triangles += 1
    return triangles


def _spread_score(ys: list[float]) -> float:
    if len(ys) < 2:
        return 50.0
    mean = sum(ys) / len(ys)
    var = sum((y - mean) ** 2 for y in ys) / len(ys)
    # Compact ~ low variance; stretched ~ high. Map to 0–100.
    return max(0.0, min(100.0, math.sqrt(var) * 4.0))


def _pressing_score(*, def_actions: int, opp_passes: int) -> float:
    """Higher is better pressing (fewer opponent passes per defensive action)."""

    if def_actions <= 0:
        return 0.0
    ppda = opp_passes / def_actions
    # Opta-ish PPDA: lower is more intense. Map 20→0, 4→100.
    return max(0.0, min(100.0, (20.0 - ppda) * (100.0 / 16.0)))


def _set_piece_tally(
    events: Sequence[MatchEvent],
    *,
    team_id: UUID,
) -> _SetPieceTally:
    """Count deliveries, first-contact wins, and second-phase shots for a team.

    Attacking credit: own restarts → own first contact / own shots in window.
    Defensive credit: opponent restarts → our first-contact clears/duels.
    """

    ordered = sorted(events, key=_clock_s)
    deliveries = 0
    first_contact = 0
    second_phase = 0
    for index, restart in enumerate(ordered):
        if restart.event_type not in SET_PIECE_TYPES:
            continue
        t0 = _clock_s(restart)
        attacking = restart.team_id == team_id
        if attacking:
            deliveries += 1
        phase1_claimed = False
        for follow in ordered[index + 1 :]:
            dt = _clock_s(follow) - t0
            if dt < 0.0:
                continue
            if dt > PHASE2_WINDOW_S:
                break
            if (
                not phase1_claimed
                and dt <= PHASE1_WINDOW_S
                and follow.event_type in FIRST_CONTACT_TYPES
            ):
                if attacking and follow.team_id == team_id and follow.successful:
                    first_contact += 1
                    phase1_claimed = True
                elif (
                    not attacking
                    and follow.team_id == team_id
                    and follow.successful
                ):
                    # Defensive first contact on opponent delivery.
                    first_contact += 1
                    phase1_claimed = True
            if (
                attacking
                and follow.team_id == team_id
                and (
                    follow.event_type in PHASE2_SHOT_TYPES
                    or follow.is_goal
                )
            ):
                second_phase += 1
    return _SetPieceTally(
        deliveries=deliveries,
        first_contact=first_contact,
        second_phase=second_phase,
    )


def _player_set_piece_tally(
    events: Sequence[MatchEvent],
    *,
    player_id: UUID,
    team_id: UUID,
) -> _SetPieceTally:
    """Per-player set-piece roles: taker, first contact, second-phase shot."""

    ordered = sorted(events, key=_clock_s)
    deliveries = 0
    first_contact = 0
    second_phase = 0
    for index, restart in enumerate(ordered):
        if restart.event_type not in SET_PIECE_TYPES:
            continue
        t0 = _clock_s(restart)
        if restart.player_id == player_id:
            deliveries += 1
        phase1_claimed = False
        for follow in ordered[index + 1 :]:
            dt = _clock_s(follow) - t0
            if dt < 0.0:
                continue
            if dt > PHASE2_WINDOW_S:
                break
            if (
                not phase1_claimed
                and dt <= PHASE1_WINDOW_S
                and follow.event_type in FIRST_CONTACT_TYPES
                and follow.successful
            ):
                # Only one first-contact winner per restart; credit the actor.
                phase1_claimed = True
                if follow.player_id == player_id:
                    first_contact += 1
            if (
                follow.player_id == player_id
                and follow.team_id == team_id
                and (
                    follow.event_type in PHASE2_SHOT_TYPES
                    or follow.is_goal
                )
            ):
                second_phase += 1
    return _SetPieceTally(
        deliveries=deliveries,
        first_contact=first_contact,
        second_phase=second_phase,
    )


def _influence_iq(
    *,
    halos: int,
    triangles: int,
    recoveries: int,
    alleys: int,
    pressing: float,
    compact: float,
    set_piece_deliveries: int = 0,
    set_piece_first_contact: int = 0,
    set_piece_second_phase: int = 0,
) -> float:
    # Set-piece weight mirrors research: first contact + second phase outrank
    # raw delivery volume (taking many corners ≠ creating threat).
    set_piece_raw = (
        min(set_piece_deliveries, 20) * 0.6
        + min(set_piece_first_contact, 25) * 1.4
        + min(set_piece_second_phase, 15) * 2.0
    )
    raw = (
        min(halos, 40) * 0.8
        + min(triangles, 30) * 1.1
        + min(recoveries, 40) * 0.9
        + min(alleys, 25) * 1.0
        + pressing * 0.25
        + (100.0 - abs(compact - 35.0)) * 0.15
        + set_piece_raw
    )
    return round(max(0.0, min(100.0, raw / 1.85)), 1)


def offball_team_stats(rundown: MatchRundown) -> list[OffBallTeamStats]:
    """Fold tag events into Impact-like off-ball team rows (home then away)."""

    sheets = team_sheets_from_rundown(rundown)
    if not sheets:
        return []
    events = list(rundown.events)
    out: list[OffBallTeamStats] = []
    for sheet in sheets:
        team_id = sheet.team_id
        owned = [event for event in events if event.team_id == team_id]
        opp = [event for event in events if event.team_id != team_id]
        recoveries = [
            event
            for event in owned
            if event.event_type is EventType.BALL_RECOVERY
            and _third(event.x, attacking_right=bool(event.attacking_left_to_right))
            in {"defensive", "middle"}
        ]
        halos = sum(
            1
            for event in owned
            if event.event_type
            in {
                EventType.BALL_RECOVERY,
                EventType.INTERCEPTION,
                EventType.BLOCK_SHOT,
                EventType.BLOCK_CROSS,
            }
            or (event.event_type in PASS_TYPES and event.is_progressive and event.successful)
        )
        att_tri = _count_pass_triangles(events, team_id=team_id)
        # Defensive triangles: opponent chains broken by our recovery/intercept.
        def_tri = sum(
            1
            for event in owned
            if event.event_type in {EventType.INTERCEPTION, EventType.BALL_RECOVERY}
        ) // 2
        recovery_ys = [event.y for event in recoveries] or [
            event.y for event in owned if event.event_type in DEF_ACTIONS
        ]
        stretched = _spread_score(recovery_ys)
        compact = max(0.0, min(100.0, 100.0 - stretched))
        alleys = sum(
            1
            for event in owned
            if event.event_type in PASS_TYPES
            and event.successful
            and event.is_progressive
            and _is_half_space(event.y)
            and (event.end_y is None or _is_half_space(float(event.end_y)))
        )
        def_actions = sum(1 for event in owned if event.event_type in DEF_ACTIONS)
        opp_passes = sum(1 for event in opp if event.event_type in PASS_TYPES)
        pressing = _pressing_score(def_actions=def_actions, opp_passes=opp_passes)
        set_pieces = _set_piece_tally(events, team_id=team_id)
        iq = _influence_iq(
            halos=halos,
            triangles=att_tri + def_tri,
            recoveries=len(recoveries),
            alleys=alleys,
            pressing=pressing,
            compact=compact,
            set_piece_deliveries=set_pieces.deliveries,
            set_piece_first_contact=set_pieces.first_contact,
            set_piece_second_phase=set_pieces.second_phase,
        )
        out.append(
            OffBallTeamStats(
                team_id=team_id,
                team_name=sheet.team_name,
                player_halos=halos,
                attacking_triangles=att_tri,
                defensive_triangles=def_tri,
                compact_defence=round(compact, 1),
                stretched_defence=round(stretched, 1),
                uncontested_alleys=alleys,
                defensive_recoveries=len(recoveries),
                pressing_ability=round(pressing, 1),
                set_piece_deliveries=set_pieces.deliveries,
                set_piece_first_contact=set_pieces.first_contact,
                set_piece_second_phase=set_pieces.second_phase,
                influence_iq=iq,
            )
        )
    return out


def offball_player_stats(rundown: MatchRundown) -> list[OffBallPlayerStats]:
    """Per-player Influence IQ from recoveries, progressive lanes, and pressing."""

    events = list(rundown.events)
    by_player: dict[UUID, list[MatchEvent]] = defaultdict(list)
    for event in events:
        if event.player_id is None:
            continue
        by_player[event.player_id].append(event)
    profile_by_id = {profile.player_id: profile for profile in rundown.players}
    rows: list[OffBallPlayerStats] = []
    for player_id, owned in by_player.items():
        profile = profile_by_id.get(player_id)
        if profile is None:
            continue
        recoveries = sum(1 for event in owned if event.event_type is EventType.BALL_RECOVERY)
        intercepts = sum(1 for event in owned if event.event_type is EventType.INTERCEPTION)
        blocks = sum(
            1
            for event in owned
            if event.event_type
            in {EventType.BLOCK_SHOT, EventType.BLOCK_CROSS, EventType.BLOCK_PASS}
        )
        progressive = sum(
            1
            for event in owned
            if event.event_type in PASS_TYPES and event.successful and event.is_progressive
        )
        alleys = sum(
            1
            for event in owned
            if event.event_type in PASS_TYPES
            and event.successful
            and event.is_progressive
            and _is_half_space(event.y)
        )
        pressing_actions = sum(1 for event in owned if event.event_type in DEF_ACTIONS)
        triangles = _count_pass_triangles(owned, team_id=profile.team_id)
        halo = recoveries + intercepts + blocks + progressive
        pressing = _pressing_score(
            def_actions=max(pressing_actions, 1),
            opp_passes=max(8, 40 - pressing_actions),
        )
        set_pieces = _player_set_piece_tally(
            events, player_id=player_id, team_id=profile.team_id
        )
        iq = _influence_iq(
            halos=halo,
            triangles=triangles,
            recoveries=recoveries,
            alleys=alleys,
            pressing=pressing,
            compact=50.0,
            set_piece_deliveries=set_pieces.deliveries,
            set_piece_first_contact=set_pieces.first_contact,
            set_piece_second_phase=set_pieces.second_phase,
        )
        rows.append(
            OffBallPlayerStats(
                player_id=player_id,
                team_id=profile.team_id,
                player_name=profile.player_name or "Player",
                jersey_number=profile.jersey_number,
                position=profile.position or "",
                player_halo=halo,
                attacking_triangles=triangles,
                defensive_recoveries=recoveries,
                uncontested_alleys=alleys,
                pressing_actions=pressing_actions,
                set_piece_deliveries=set_pieces.deliveries,
                set_piece_first_contact=set_pieces.first_contact,
                set_piece_second_phase=set_pieces.second_phase,
                influence_iq=iq,
            )
        )
    rows.sort(key=lambda row: (-row.influence_iq, row.player_name))
    return rows


def offball_team_rows(teams: Sequence[OffBallTeamStats]) -> list[dict[str, object]]:
    """Transpose off-ball team stats for the Match Statistics UI."""

    if not teams:
        return []
    fields = (
        ("Influence IQ", "influence_iq"),
        ("Player Halos", "player_halos"),
        ("Attacking Triangles", "attacking_triangles"),
        ("Defensive Triangles", "defensive_triangles"),
        ("Compact Defence", "compact_defence"),
        ("Stretched Defence", "stretched_defence"),
        ("Uncontested Alleys", "uncontested_alleys"),
        ("Defensive Recoveries", "defensive_recoveries"),
        ("Pressing Ability", "pressing_ability"),
        ("Set-Piece Deliveries", "set_piece_deliveries"),
        ("Set-Piece First Contact", "set_piece_first_contact"),
        ("Set-Piece Second Phase", "set_piece_second_phase"),
    )
    rows: list[dict[str, object]] = []
    for label, attr in fields:
        row: dict[str, object] = {"Stat": label}
        for team in teams:
            row[team.team_name] = getattr(team, attr)
        rows.append(row)
    return rows


def offball_player_rows(players: Sequence[OffBallPlayerStats]) -> list[dict[str, object]]:
    """Table rows for the Individual Off-ball log."""

    return [
        {
            "Player": row.player_name,
            "#": row.jersey_number or "—",
            "Pos": row.position or "—",
            "IQ": row.influence_iq,
            "Halo": row.player_halo,
            "Triangles": row.attacking_triangles,
            "Recoveries": row.defensive_recoveries,
            "Alleys": row.uncontested_alleys,
            "Press": row.pressing_actions,
            "SP Del": row.set_piece_deliveries,
            "SP 1st": row.set_piece_first_contact,
            "SP 2nd": row.set_piece_second_phase,
        }
        for row in players
    ]
