"""Bundled sample match: Napoleon Bot vs 80s Jeans.

A dumb, detailed 90-minute two-team sheet so the dashboard can show
collective boards and full individual player pages without an upload.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from analytics.game_ingest import GamePayload, PlayerRosterEntry
from data_models.events import EventType, MatchEvent, ShotOutcome

SIM_MATCH_ID: UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
HOME_TEAM_ID: UUID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
AWAY_TEAM_ID: UUID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
# Kept for older tests / dummy catalog wiring.
TEAM_ID = HOME_TEAM_ID
PLAYMAKER_ID: UUID = UUID("11111111-1111-1111-1111-111111111111")
STRIKER_ID: UUID = UUID("22222222-2222-2222-2222-222222222222")
DEFENDER_ID: UUID = UUID("33333333-3333-3333-3333-333333333333")

HOME_NAME = "Napoleon Bot"
AWAY_NAME = "80s Jeans"


def _pid(label: str) -> UUID:
    return uuid5(SIM_MATCH_ID, f"player:{label}")


def _stamp(*, period: int, minute: int, second: int) -> int:
    return ((period - 1) * 45 + minute) * 60_000 + second * 1_000


def _event(
    *,
    team_id: UUID,
    player_id: UUID,
    period: int,
    minute: int,
    second: int,
    event_type: EventType,
    x: float,
    y: float,
    **overrides: Any,
) -> MatchEvent:
    payload: dict[str, Any] = {
        "match_id": SIM_MATCH_ID,
        "team_id": team_id,
        "player_id": player_id,
        "period": period,
        "minute": minute,
        "second": second,
        "event_type": event_type,
        "x": x,
        "y": y,
        "successful": True,
        "video_timestamp_ms": _stamp(period=period, minute=minute, second=second),
        "clip_url": (
            f"https://clips.enjoystats.local/{SIM_MATCH_ID}/"
            f"{_stamp(period=period, minute=minute, second=second)}.mp4"
        ),
    }
    payload.update(overrides)
    return MatchEvent.model_validate(payload)


def _squad(
    team_id: UUID,
    rows: list[tuple[UUID, int, str, str]],
) -> list[PlayerRosterEntry]:
    return [
        PlayerRosterEntry(
            player_id=player_id,
            team_id=team_id,
            jersey_number=jersey,
            player_name=name,
            position=position,
        )
        for player_id, jersey, name, position in rows
    ]


def sample_roster() -> list[PlayerRosterEntry]:
    """15 Napoleon Bot + 15 80s Jeans identities (starters and subs)."""

    home = _squad(
        HOME_TEAM_ID,
        [
            (_pid("nb-gk"), 1, "Binary Gloves", "GK"),
            (_pid("nb-rb"), 2, "Right Click", "RB"),
            (DEFENDER_ID, 5, "Chris Stopper", "CB"),
            (_pid("nb-cb2"), 4, "Firewall", "CB"),
            (_pid("nb-lb"), 3, "Left Margin", "LB"),
            (_pid("nb-cdm"), 6, "Cache Control", "CDM"),
            (_pid("nb-cm"), 7, "Algo Mid", "CM"),
            (PLAYMAKER_ID, 8, "Alex Playmaker", "CAM"),
            (_pid("nb-rw"), 11, "Pixel Wing", "RW"),
            (STRIKER_ID, 9, "Sam Striker", "ST"),
            (_pid("nb-lw"), 10, "Laggy Left", "LW"),
            (_pid("nb-sub1"), 14, "Boot Loop", "CM"),
            (_pid("nb-sub2"), 15, "Hotfix", "ST"),
            (_pid("nb-sub3"), 16, "Soft Reset", "RB"),
            (_pid("nb-sub4"), 17, "Blue Screen", "LW"),
        ],
    )
    away = _squad(
        AWAY_TEAM_ID,
        [
            (_pid("aj-gk"), 1, "Neon Keeper", "GK"),
            (_pid("aj-rb"), 2, "Acid Wash", "RB"),
            (_pid("aj-cb1"), 5, "Stonewash", "CB"),
            (_pid("aj-cb2"), 4, "Raw Denim", "CB"),
            (_pid("aj-lb"), 3, "Cuff Roll", "LB"),
            (_pid("aj-cdm"), 6, "Belt Loop", "CDM"),
            (_pid("aj-cm"), 8, "Synth Wave", "CM"),
            (_pid("aj-cam"), 10, "Walkman", "CAM"),
            (_pid("aj-rw"), 7, "Cassette", "RW"),
            (_pid("aj-st"), 9, "Mullet Strike", "ST"),
            (_pid("aj-lw"), 11, "Parachute", "LW"),
            (_pid("aj-sub1"), 14, "Jeggings", "CM"),
            (_pid("aj-sub2"), 15, "High Top", "ST"),
            (_pid("aj-sub3"), 16, "Fanny Pack", "RB"),
            (_pid("aj-sub4"), 17, "Scrunchie", "LW"),
        ],
    )
    return home + away


def _clock(minute_abs: int) -> tuple[int, int, int]:
    """Map 0–89 absolute minutes onto period / minute / second."""

    clamped = max(0, min(89, minute_abs))
    period = 1 if clamped < 45 else 2
    minute = clamped if period == 1 else clamped - 45
    second = (clamped * 17) % 60
    return period, minute, second


def sample_events() -> list[MatchEvent]:
    """A full-match tag sheet covering both squads and all four pillars."""

    nb = {
        row.jersey_number: row.player_id for row in sample_roster() if row.team_id == HOME_TEAM_ID
    }
    aj = {
        row.jersey_number: row.player_id for row in sample_roster() if row.team_id == AWAY_TEAM_ID
    }
    events: list[MatchEvent] = []

    def add(
        team: UUID,
        player: UUID,
        minute_abs: int,
        event_type: EventType,
        x: float,
        y: float,
        **overrides: Any,
    ) -> None:
        period, minute, second = _clock(minute_abs)
        events.append(
            _event(
                team_id=team,
                player_id=player,
                period=period,
                minute=minute,
                second=second,
                event_type=event_type,
                x=x,
                y=y,
                **overrides,
            )
        )

    # Regular rhythm of passes / recoveries for both sides across 90 minutes.
    home_passers = [8, 7, 6, 11, 10, 9, 5, 4, 2, 3, 14, 15]
    away_passers = [10, 8, 6, 7, 11, 9, 5, 4, 2, 3, 14, 15]
    for minute_abs in range(0, 90, 2):
        hp = home_passers[minute_abs % len(home_passers)]
        ap = away_passers[(minute_abs // 2) % len(away_passers)]
        progressive = minute_abs % 6 == 0
        add(
            HOME_TEAM_ID,
            nb[hp],
            minute_abs,
            EventType.PASS,
            40.0 + (minute_abs % 40),
            30.0 + (minute_abs % 40),
            end_x=55.0 + (minute_abs % 30),
            end_y=45.0,
            is_progressive=progressive,
            successful=minute_abs % 11 != 0,
        )
        add(
            AWAY_TEAM_ID,
            aj[ap],
            minute_abs + 1,
            EventType.PASS,
            60.0 - (minute_abs % 35),
            55.0,
            end_x=45.0,
            end_y=50.0,
            is_progressive=minute_abs % 8 == 0,
            successful=minute_abs % 13 != 0,
        )

    # Set pieces, duels, defensive actions sprinkled through the match.
    for minute_abs, jersey, kind, x, y in (
        (4, 5, EventType.INTERCEPTION, 22.0, 48.0),
        (9, 6, EventType.BALL_RECOVERY, 35.0, 40.0),
        (12, 2, EventType.THROW_IN, 5.0, 15.0),
        (18, 11, EventType.CROSS, 78.0, 12.0),
        (22, 8, EventType.CORNER, 99.0, 5.0),
        (27, 5, EventType.AERIAL_DUEL, 48.0, 50.0),
        (31, 6, EventType.GROUND_DUEL, 42.0, 44.0),
        (36, 4, EventType.FOUL_COMMITTED, 55.0, 40.0),
        (39, 8, EventType.FOUL_WON, 60.0, 48.0),
        (42, 3, EventType.BALL_LOST, 30.0, 60.0),
        (48, 14, EventType.PASS, 50.0, 50.0),
        (55, 15, EventType.SHOT, 84.0, 48.0),
        (62, 16, EventType.THROW_IN, 5.0, 80.0),
        (70, 17, EventType.CROSS, 80.0, 88.0),
        (77, 7, EventType.FREE_KICK, 66.0, 50.0),
        (84, 5, EventType.BLOCK_SHOT, 16.0, 50.0),
        (88, 1, EventType.SAVE, 8.0, 50.0),
    ):
        extras: dict[str, Any] = {}
        if kind in {
            EventType.CROSS,
            EventType.PASS,
            EventType.THROW_IN,
            EventType.CORNER,
            EventType.FREE_KICK,
            EventType.ASSIST,
        }:
            extras.update(end_x=min(99.0, x + 12.0), end_y=y)
        if kind is EventType.CROSS:
            extras.update(end_x=92.0, end_y=50.0)
        if kind is EventType.SHOT:
            extras.update(
                end_x=100.0,
                end_y=50.0,
                shot_outcome=ShotOutcome.ON_TARGET,
                successful=True,
            )
        if kind is EventType.FREE_KICK:
            extras.update(end_x=88.0, end_y=50.0)
        if kind is EventType.AERIAL_DUEL:
            extras.update(successful=True)
        add(HOME_TEAM_ID, nb[jersey], minute_abs, kind, x, y, **extras)

    for minute_abs, jersey, kind, x, y in (
        (6, 5, EventType.INTERCEPTION, 70.0, 52.0),
        (14, 6, EventType.BALL_RECOVERY, 58.0, 45.0),
        (20, 3, EventType.THROW_IN, 95.0, 20.0),
        (25, 7, EventType.CROSS, 22.0, 18.0),
        (33, 10, EventType.CORNER, 1.0, 95.0),
        (40, 4, EventType.AERIAL_DUEL, 50.0, 50.0),
        (46, 8, EventType.GROUND_DUEL, 55.0, 40.0),
        (52, 14, EventType.PASS, 48.0, 48.0),
        (58, 15, EventType.SHOT, 18.0, 52.0),
        (66, 11, EventType.FOUL_WON, 40.0, 50.0),
        (72, 6, EventType.FOUL_COMMITTED, 45.0, 55.0),
        (79, 16, EventType.BALL_LOST, 65.0, 30.0),
        (85, 1, EventType.SAVE, 92.0, 48.0),
        (87, 5, EventType.BLOCK_CROSS, 85.0, 20.0),
    ):
        extras: dict[str, Any] = {}
        if kind in {
            EventType.CROSS,
            EventType.PASS,
            EventType.THROW_IN,
            EventType.CORNER,
            EventType.FREE_KICK,
            EventType.ASSIST,
        }:
            extras.update(end_x=max(1.0, x - 12.0), end_y=y)
        if kind is EventType.CROSS:
            extras.update(end_x=10.0, end_y=50.0)
        if kind is EventType.SHOT:
            extras.update(
                end_x=0.0,
                end_y=50.0,
                shot_outcome=ShotOutcome.MISSED,
                successful=False,
            )
        add(AWAY_TEAM_ID, aj[jersey], minute_abs, kind, x, y, **extras)

    # Tight set-piece phase chains (delivery → first contact → second-phase shot)
    # so Influence IQ can credit Phase 1 / Phase 2 windows.
    # Home corner at abs min 22 → second = (22 * 17) % 60 = 14.
    events.append(
        _event(
            team_id=HOME_TEAM_ID,
            player_id=nb[5],
            period=1,
            minute=22,
            second=18,
            event_type=EventType.AERIAL_DUEL,
            x=92.0,
            y=48.0,
            successful=True,
        )
    )
    events.append(
        _event(
            team_id=HOME_TEAM_ID,
            player_id=nb[9],
            period=1,
            minute=22,
            second=26,
            event_type=EventType.SHOT,
            x=90.0,
            y=50.0,
            end_x=100.0,
            end_y=50.0,
            shot_outcome=ShotOutcome.MISSED,
            successful=False,
        )
    )
    # Away corner at abs min 33 → second = (33 * 17) % 60 = 21; home CB clears.
    events.append(
        _event(
            team_id=HOME_TEAM_ID,
            player_id=nb[5],
            period=1,
            minute=33,
            second=25,
            event_type=EventType.AERIAL_DUEL,
            x=12.0,
            y=50.0,
            successful=True,
        )
    )
    # Home free kick at abs min 77 → period 2, minute 32, second = (77 * 17) % 60 = 49.
    events.append(
        _event(
            team_id=HOME_TEAM_ID,
            player_id=nb[9],
            period=2,
            minute=32,
            second=55,
            event_type=EventType.SHOT,
            x=86.0,
            y=48.0,
            end_x=100.0,
            end_y=52.0,
            shot_outcome=ShotOutcome.ON_TARGET,
            successful=True,
        )
    )

    # Goals: Napoleon Bot 3 – 80s Jeans 2
    add(
        HOME_TEAM_ID,
        nb[9],
        16,
        EventType.GOAL,
        88.0,
        48.0,
        is_goal=True,
        shot_outcome=ShotOutcome.ON_TARGET,
    )
    add(HOME_TEAM_ID, nb[8], 16, EventType.ASSIST, 72.0, 50.0, end_x=88.0, end_y=48.0)
    add(
        AWAY_TEAM_ID,
        aj[9],
        29,
        EventType.GOAL,
        12.0,
        52.0,
        is_goal=True,
        shot_outcome=ShotOutcome.ON_TARGET,
    )
    add(AWAY_TEAM_ID, aj[10], 29, EventType.ASSIST, 28.0, 48.0, end_x=12.0, end_y=52.0)
    add(HOME_TEAM_ID, nb[1], 29, EventType.GOAL_CONCEDED, 6.0, 50.0)
    add(
        HOME_TEAM_ID,
        nb[11],
        54,
        EventType.GOAL,
        90.0,
        40.0,
        is_goal=True,
        shot_outcome=ShotOutcome.ON_TARGET,
    )
    add(HOME_TEAM_ID, nb[8], 54, EventType.ASSIST, 75.0, 35.0, end_x=90.0, end_y=40.0)
    add(
        AWAY_TEAM_ID,
        aj[15],
        68,
        EventType.GOAL,
        10.0,
        55.0,
        is_goal=True,
        shot_outcome=ShotOutcome.ON_TARGET,
    )
    add(HOME_TEAM_ID, nb[1], 68, EventType.GOAL_CONCEDED, 6.0, 50.0)
    add(
        HOME_TEAM_ID,
        nb[15],
        81,
        EventType.GOAL,
        86.0,
        58.0,
        is_goal=True,
        shot_outcome=ShotOutcome.ON_TARGET,
    )
    add(HOME_TEAM_ID, nb[14], 81, EventType.ASSIST, 70.0, 60.0, end_x=86.0, end_y=58.0)

    # Extra shots / offsides / cards so individual sheets look alive.
    add(
        HOME_TEAM_ID,
        nb[9],
        8,
        EventType.SHOT,
        82.0,
        45.0,
        end_x=100.0,
        end_y=48.0,
        shot_outcome=ShotOutcome.MISSED,
        successful=False,
    )
    add(
        HOME_TEAM_ID,
        nb[10],
        44,
        EventType.SHOT,
        85.0,
        60.0,
        end_x=100.0,
        end_y=55.0,
        shot_outcome=ShotOutcome.BLOCKED,
        successful=False,
    )
    add(HOME_TEAM_ID, nb[9], 50, EventType.OFFSIDE, 88.0, 50.0, successful=False)
    add(AWAY_TEAM_ID, aj[9], 61, EventType.OFFSIDE, 12.0, 48.0, successful=False)
    add(HOME_TEAM_ID, nb[6], 74, EventType.YELLOW_CARD, 48.0, 48.0)
    add(AWAY_TEAM_ID, aj[6], 83, EventType.YELLOW_CARD, 52.0, 52.0)
    add(
        AWAY_TEAM_ID,
        aj[7],
        35,
        EventType.SHOT,
        20.0,
        40.0,
        end_x=0.0,
        end_y=45.0,
        shot_outcome=ShotOutcome.ON_TARGET,
    )
    add(
        HOME_TEAM_ID,
        nb[8],
        89,
        EventType.PASS,
        55.0,
        50.0,
        end_x=70.0,
        end_y=50.0,
        is_progressive=True,
    )

    events.sort(
        key=lambda event: (event.period, event.minute, event.second, event.event_type.value)
    )
    return events


def sample_game_payload() -> GamePayload:
    """Roster plus events for one-click collection on the dashboard."""

    return GamePayload(
        match_id=SIM_MATCH_ID,
        players=sample_roster(),
        events=sample_events(),
        home_team_name=HOME_NAME,
        away_team_name=AWAY_NAME,
        tag_source="sample",
    )
