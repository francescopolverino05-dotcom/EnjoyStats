"""Pydantic schemas for football event tagging and player statistics."""

from data_models.events import EventType, MatchEvent, TaggedMatchEvent
from data_models.player_stats import (
    AttemptSplit,
    BallRecoveryStats,
    BlockStats,
    DefensiveStats,
    DistributionStats,
    FoulStats,
    InterceptionStats,
    OffensiveStats,
    PassLocationStats,
    PlayerMatchStats,
    StrictModel,
)

__all__ = [
    "AttemptSplit",
    "BallRecoveryStats",
    "BlockStats",
    "DefensiveStats",
    "DistributionStats",
    "EventType",
    "FoulStats",
    "InterceptionStats",
    "MatchEvent",
    "OffensiveStats",
    "PassLocationStats",
    "PlayerMatchStats",
    "StrictModel",
    "TaggedMatchEvent",
]
