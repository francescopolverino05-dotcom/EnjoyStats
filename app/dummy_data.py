"""Stakeholder-preview profiles used when FastAPI is unreachable."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final
from uuid import UUID

from data_models.player_stats import (
    AttemptSplit,
    BallRecoveryStats,
    DefensiveStats,
    DistributionStats,
    OffensiveStats,
    PassLocationStats,
    PlayerMatchProfile,
)

from app.metrics import PassDirections

SIM_MATCH_ID: Final[UUID] = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SHOWCASE_MATCH_ID: Final[UUID] = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
PLAYMAKER_ID: Final[UUID] = UUID("11111111-1111-1111-1111-111111111111")
STRIKER_ID: Final[UUID] = UUID("22222222-2222-2222-2222-222222222222")
CONTROLLER_ID: Final[UUID] = UUID("44444444-4444-4444-4444-444444444444")
TEAM_ID: Final[UUID] = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

_KICKOFF = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


def _playmaker_profile() -> PlayerMatchProfile:
    """Outcome of the 5-second mock attacking move (playmaker)."""

    return PlayerMatchProfile(
        match_id=SIM_MATCH_ID,
        player_id=PLAYMAKER_ID,
        team_id=TEAM_ID,
        jersey_number=8,
        position="CAM",
        offensive=OffensiveStats(minutes=0.1, assists=1),
        defensive=DefensiveStats(
            aerial_duels=AttemptSplit(success=1, total=1),
            ground_duels=AttemptSplit(success=0, total=0),
            ball_recoveries=BallRecoveryStats(middle_third=1),
        ),
        distribution=DistributionStats(
            passes=AttemptSplit(success=3, total=3),
            progressive_passes=AttemptSplit(success=3, total=3),
            pass_locations=PassLocationStats(
                short=AttemptSplit(success=2, total=2),
                medium=AttemptSplit(success=1, total=1),
            ),
        ),
        collected_at=_KICKOFF,
    )


def _striker_profile() -> PlayerMatchProfile:
    """Outcome of the 5-second mock attacking move (striker)."""

    return PlayerMatchProfile(
        match_id=SIM_MATCH_ID,
        player_id=STRIKER_ID,
        team_id=TEAM_ID,
        jersey_number=9,
        position="ST",
        offensive=OffensiveStats(
            minutes=0.1,
            goals=1,
            total_shots=1,
            shots_on_target=1,
            shots_inside_penalty_area=1,
        ),
        defensive=DefensiveStats(
            ground_duels=AttemptSplit(success=1, total=1),
            aerial_duels=AttemptSplit(success=0, total=1),
            ball_recoveries=BallRecoveryStats(final_third=1),
        ),
        distribution=DistributionStats(
            passes=AttemptSplit(success=1, total=1),
            cutbacks=AttemptSplit(success=0, total=0),
            pass_locations=PassLocationStats(short=AttemptSplit(success=1, total=1)),
        ),
        collected_at=_KICKOFF,
    )


def _controller_profile() -> PlayerMatchProfile:
    """Richer midfield showcase used for stakeholder walkthroughs."""

    return PlayerMatchProfile(
        match_id=SHOWCASE_MATCH_ID,
        player_id=CONTROLLER_ID,
        team_id=TEAM_ID,
        jersey_number=6,
        position="CM",
        offensive=OffensiveStats(
            minutes=78.5,
            goals=1,
            assists=2,
            total_shots=4,
            shots_on_target=2,
            blocked_shots=1,
            missed_shots=1,
            shots_inside_penalty_area=2,
            shots_outside_penalty_area=2,
        ),
        defensive=DefensiveStats(
            aerial_duels=AttemptSplit(success=5, total=8),
            ground_duels=AttemptSplit(success=9, total=12),
            ball_recoveries=BallRecoveryStats(
                defensive_third=4,
                middle_third=7,
                final_third=2,
            ),
        ),
        distribution=DistributionStats(
            passes=AttemptSplit(success=48, total=55),
            crosses=AttemptSplit(success=3, total=6),
            cutbacks=AttemptSplit(success=2, total=3),
            progressive_passes=AttemptSplit(success=11, total=14),
            pass_locations=PassLocationStats(
                short=AttemptSplit(success=22, total=24),
                medium=AttemptSplit(success=18, total=21),
                long=AttemptSplit(success=8, total=10),
                into_penalty_area=AttemptSplit(success=5, total=8),
            ),
        ),
        collected_at=_KICKOFF,
    )


DUMMY_PROFILES: Final[dict[tuple[UUID, UUID], PlayerMatchProfile]] = {
    (SIM_MATCH_ID, PLAYMAKER_ID): _playmaker_profile(),
    (SIM_MATCH_ID, STRIKER_ID): _striker_profile(),
    (SHOWCASE_MATCH_ID, CONTROLLER_ID): _controller_profile(),
}

DUMMY_DIRECTIONS: Final[dict[tuple[UUID, UUID], PassDirections]] = {
    (SIM_MATCH_ID, PLAYMAKER_ID): PassDirections(forward=3, sideways=0, backward=0),
    (SIM_MATCH_ID, STRIKER_ID): PassDirections(forward=0, sideways=1, backward=0),
    (SHOWCASE_MATCH_ID, CONTROLLER_ID): PassDirections(forward=28, sideways=19, backward=8),
}


def catalog() -> list[dict[str, object]]:
    """Sidebar options: one entry per dummy/live-known player."""

    return [
        {
            "match_id": SIM_MATCH_ID,
            "match_label": "Demo — 5s attacking move",
            "player_id": PLAYMAKER_ID,
            "player_label": "#8 CAM Playmaker",
        },
        {
            "match_id": SIM_MATCH_ID,
            "match_label": "Demo — 5s attacking move",
            "player_id": STRIKER_ID,
            "player_label": "#9 ST Striker",
        },
        {
            "match_id": SHOWCASE_MATCH_ID,
            "match_label": "Showcase — midfield controller",
            "player_id": CONTROLLER_ID,
            "player_label": "#6 CM Controller",
        },
    ]


def fallback_profile(match_id: UUID, player_id: UUID) -> PlayerMatchProfile:
    """Return a canned profile, or a clean generic row for unknown IDs."""

    cached = DUMMY_PROFILES.get((match_id, player_id))
    if cached is not None:
        return cached
    return PlayerMatchProfile(
        match_id=match_id,
        player_id=player_id,
        team_id=TEAM_ID,
        jersey_number=10,
        position="AM",
        offensive=OffensiveStats(
            minutes=64.0,
            goals=1,
            assists=1,
            total_shots=3,
            shots_on_target=2,
            missed_shots=1,
            shots_inside_penalty_area=2,
            shots_outside_penalty_area=1,
        ),
        defensive=DefensiveStats(
            aerial_duels=AttemptSplit(success=3, total=5),
            ground_duels=AttemptSplit(success=4, total=6),
            ball_recoveries=BallRecoveryStats(
                defensive_third=2,
                middle_third=3,
                final_third=1,
            ),
        ),
        distribution=DistributionStats(
            passes=AttemptSplit(success=20, total=24),
            progressive_passes=AttemptSplit(success=6, total=8),
            cutbacks=AttemptSplit(success=1, total=2),
            pass_locations=PassLocationStats(
                short=AttemptSplit(success=10, total=12),
                medium=AttemptSplit(success=7, total=8),
                long=AttemptSplit(success=3, total=4),
            ),
        ),
    )


def fallback_directions(match_id: UUID, player_id: UUID) -> PassDirections:
    """Direction split paired with :func:`fallback_profile`."""

    cached = DUMMY_DIRECTIONS.get((match_id, player_id))
    if cached is not None:
        return cached
    return PassDirections(forward=12, sideways=8, backward=4)
