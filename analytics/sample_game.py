"""Bundled sample match used when a developer has no export to upload."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from analytics.game_ingest import GamePayload, PlayerRosterEntry
from data_models.events import EventType, MatchEvent, ShotOutcome

SIM_MATCH_ID: UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TEAM_ID: UUID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PLAYMAKER_ID: UUID = UUID("11111111-1111-1111-1111-111111111111")
STRIKER_ID: UUID = UUID("22222222-2222-2222-2222-222222222222")
DEFENDER_ID: UUID = UUID("33333333-3333-3333-3333-333333333333")


def _stamp(*, period: int, minute: int, second: int) -> int:
    return ((period - 1) * 45 + minute) * 60_000 + second * 1_000


def _event(
    *,
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
        "team_id": TEAM_ID,
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


def sample_roster() -> list[PlayerRosterEntry]:
    """CAM / ST / CB identities for the bundled sample match."""

    return [
        PlayerRosterEntry(
            player_id=PLAYMAKER_ID,
            team_id=TEAM_ID,
            jersey_number=8,
            player_name="Alex Playmaker",
            position="CAM",
        ),
        PlayerRosterEntry(
            player_id=STRIKER_ID,
            team_id=TEAM_ID,
            jersey_number=9,
            player_name="Sam Striker",
            position="ST",
        ),
        PlayerRosterEntry(
            player_id=DEFENDER_ID,
            team_id=TEAM_ID,
            jersey_number=5,
            player_name="Chris Stopper",
            position="CB",
        ),
    ]


def sample_events() -> list[MatchEvent]:
    """A compact 90-minute AutoData feed covering all four stat pillars."""

    playmaker = PLAYMAKER_ID
    striker = STRIKER_ID
    defender = DEFENDER_ID
    return [
        _event(
            player_id=defender,
            period=1,
            minute=3,
            second=12,
            event_type=EventType.INTERCEPTION,
            x=18.0,
            y=48.0,
        ),
        _event(
            player_id=defender,
            period=1,
            minute=3,
            second=18,
            event_type=EventType.PASS,
            x=20.0,
            y=48.0,
            end_x=38.0,
            end_y=50.0,
        ),
        _event(
            player_id=playmaker,
            period=1,
            minute=8,
            second=4,
            event_type=EventType.PASS,
            x=42.0,
            y=48.0,
            end_x=55.0,
            end_y=52.0,
            is_progressive=True,
        ),
        _event(
            player_id=playmaker,
            period=1,
            minute=8,
            second=9,
            event_type=EventType.PASS,
            x=55.0,
            y=52.0,
            end_x=68.0,
            end_y=40.0,
            is_progressive=True,
        ),
        _event(
            player_id=playmaker,
            period=1,
            minute=12,
            second=22,
            event_type=EventType.CROSS,
            x=82.0,
            y=12.0,
            end_x=94.0,
            end_y=50.0,
        ),
        _event(
            player_id=striker,
            period=1,
            minute=12,
            second=24,
            event_type=EventType.SHOT,
            x=88.0,
            y=50.0,
            end_x=100.0,
            end_y=48.0,
            successful=False,
            is_goal=False,
            shot_outcome=ShotOutcome.MISSED,
        ),
        _event(
            player_id=playmaker,
            period=1,
            minute=21,
            second=40,
            event_type=EventType.BALL_RECOVERY,
            x=48.0,
            y=44.0,
        ),
        _event(
            player_id=playmaker,
            period=1,
            minute=28,
            second=11,
            event_type=EventType.THROW_IN,
            x=60.0,
            y=0.0,
            end_x=62.0,
            end_y=18.0,
        ),
        _event(
            player_id=defender,
            period=1,
            minute=33,
            second=5,
            event_type=EventType.AERIAL_DUEL,
            x=22.0,
            y=55.0,
            successful=True,
        ),
        _event(
            player_id=defender,
            period=1,
            minute=36,
            second=50,
            event_type=EventType.BLOCK_SHOT,
            x=12.0,
            y=50.0,
        ),
        _event(
            player_id=striker,
            period=1,
            minute=41,
            second=2,
            event_type=EventType.GROUND_DUEL,
            x=74.0,
            y=48.0,
            successful=True,
        ),
        _event(
            player_id=playmaker,
            period=1,
            minute=41,
            second=8,
            event_type=EventType.ASSIST,
            x=70.0,
            y=46.0,
            end_x=86.0,
            end_y=52.0,
            is_assist=True,
            is_progressive=True,
        ),
        _event(
            player_id=striker,
            period=1,
            minute=41,
            second=10,
            event_type=EventType.SHOT,
            x=88.0,
            y=52.0,
            end_x=100.0,
            end_y=50.0,
            is_goal=True,
            shot_outcome=ShotOutcome.ON_TARGET,
        ),
        _event(
            player_id=defender,
            period=2,
            minute=4,
            second=16,
            event_type=EventType.BALL_LOST,
            x=30.0,
            y=60.0,
            successful=False,
        ),
        _event(
            player_id=playmaker,
            period=2,
            minute=11,
            second=33,
            event_type=EventType.PASS,
            x=50.0,
            y=50.0,
            end_x=48.0,
            end_y=62.0,
            successful=False,
            is_progressive=False,
        ),
        _event(
            player_id=striker,
            period=2,
            minute=18,
            second=7,
            event_type=EventType.OFFSIDE,
            x=91.0,
            y=46.0,
        ),
        _event(
            player_id=striker,
            period=2,
            minute=27,
            second=44,
            event_type=EventType.SHOT,
            x=84.0,
            y=38.0,
            end_x=100.0,
            end_y=44.0,
            shot_outcome=ShotOutcome.ON_TARGET,
        ),
        _event(
            player_id=defender,
            period=2,
            minute=34,
            second=1,
            event_type=EventType.FOUL_COMMITTED,
            x=16.0,
            y=50.0,
            successful=False,
        ),
        _event(
            player_id=defender,
            period=2,
            minute=34,
            second=3,
            event_type=EventType.YELLOW_CARD,
            x=16.0,
            y=50.0,
            successful=False,
        ),
        _event(
            player_id=playmaker,
            period=2,
            minute=41,
            second=20,
            event_type=EventType.FREE_KICK,
            x=64.0,
            y=48.0,
            end_x=88.0,
            end_y=50.0,
        ),
        _event(
            player_id=striker,
            period=2,
            minute=44,
            second=12,
            event_type=EventType.SHOT,
            x=90.0,
            y=50.0,
            end_x=100.0,
            end_y=50.0,
            successful=False,
            shot_outcome=ShotOutcome.BLOCKED,
        ),
    ]


def sample_game_payload() -> GamePayload:
    """Roster plus events for one-click collection on the dashboard."""

    return GamePayload(match_id=SIM_MATCH_ID, players=sample_roster(), events=sample_events())
