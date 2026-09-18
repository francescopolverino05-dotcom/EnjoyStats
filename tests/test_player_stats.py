"""Tests for the three-pillar player statistics schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_models.player_stats import (
    AttemptSplit,
    DefensiveStats,
    DistributionStats,
    OffensiveStats,
    PassLocationStats,
    PlayerMatchStats,
)


def test_attempt_split_rejects_success_above_total() -> None:
    with pytest.raises(ValidationError, match="cannot exceed total"):
        AttemptSplit(success=2, total=1)


def test_attempt_split_rates_and_add() -> None:
    split = AttemptSplit(success=3, total=4)
    assert split.failed == 1
    assert split.success_rate == 0.75
    updated = split.add(succeeded=False)
    assert updated.success == 3
    assert updated.total == 5
    assert AttemptSplit().success_rate == 0.0


def test_offensive_shot_identities() -> None:
    stats = OffensiveStats(
        minutes=91.36,
        goals=1,
        total_shots=4,
        shots_on_target=2,
        blocked_shots=1,
        missed_shots=1,
        shots_inside_penalty_area=3,
        shots_outside_penalty_area=1,
        offsides=2,
        freekicks=1,
        corners=3,
    )
    assert stats.minutes == 91.4
    assert stats.shot_accuracy == 0.5


def test_offensive_rejects_unbalanced_shots() -> None:
    with pytest.raises(ValidationError, match="shots_on_target"):
        OffensiveStats(total_shots=2, shots_on_target=2, missed_shots=1)
    with pytest.raises(ValidationError, match="shots_inside_penalty_area"):
        OffensiveStats(
            total_shots=1,
            shots_on_target=1,
            shots_inside_penalty_area=1,
            shots_outside_penalty_area=1,
        )
    with pytest.raises(ValidationError, match="cannot exceed shots_on_target"):
        OffensiveStats(goals=2, total_shots=1, shots_on_target=1, shots_inside_penalty_area=1)


def test_record_shot_inside_box_goal() -> None:
    stats = OffensiveStats().record_shot(inside_penalty_area=True, is_goal=True)
    assert stats.goals == 1
    assert stats.total_shots == 1
    assert stats.shots_on_target == 1
    assert stats.shots_inside_penalty_area == 1
    assert stats.blocked_shots == 0
    assert stats.missed_shots == 0


def test_record_shot_requires_exclusive_outcome() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        OffensiveStats().record_shot(inside_penalty_area=False, on_target=True, blocked=True)


def test_defensive_nested_duels_and_zonal_recoveries() -> None:
    defensive = (
        DefensiveStats()
        .record_aerial_duel(succeeded=True)
        .record_ground_duel(succeeded=False)
        .record_block("shots")
        .record_foul(won=True)
        .record_foul(won=False)
        .record_interception("middle")
        .record_recovery("final")
        .record_recovery("defensive")
    )
    assert defensive.aerial_duels.success == 1
    assert defensive.aerial_duels.total == 1
    assert defensive.ground_duels.failed == 1
    assert defensive.blocks.shots == 1
    assert defensive.blocks.total == 1
    assert defensive.fouls.won == 1
    assert defensive.fouls.committed == 1
    assert defensive.interceptions.total == 1
    assert defensive.interceptions.middle_third == 1
    assert defensive.ball_recoveries.final_third == 1
    assert defensive.ball_recoveries.defensive_third == 1
    assert defensive.ball_recoveries.total == 2


def test_interceptions_require_zonal_sum_when_present() -> None:
    from data_models.player_stats import InterceptionStats

    with pytest.raises(ValidationError, match="interception thirds"):
        InterceptionStats(total=3, defensive_third=1)


def test_distribution_length_bands_reconcile() -> None:
    stats = (
        DistributionStats()
        .record_pass(succeeded=True, band="short")
        .record_pass(succeeded=False, band="medium", is_progressive=True)
        .record_pass(succeeded=True, band="long", into_penalty_area=True, is_cross=True)
    )
    assert stats.passes.total == 3
    assert stats.passes.success == 2
    assert stats.crosses.total == 1
    assert stats.progressive_passes.total == 1
    assert stats.pass_locations.short.total == 1
    assert stats.pass_locations.medium.total == 1
    assert stats.pass_locations.long.total == 1
    assert stats.pass_locations.into_penalty_area.success == 1


def test_distribution_rejects_crosses_above_passes() -> None:
    with pytest.raises(ValidationError, match="crosses.total"):
        DistributionStats(
            passes=AttemptSplit(success=0, total=1),
            crosses=AttemptSplit(success=0, total=2),
        )


def test_length_split_must_match_headline_passes() -> None:
    with pytest.raises(ValidationError, match="length split"):
        DistributionStats(
            passes=AttemptSplit(success=1, total=2),
            pass_locations=PassLocationStats(short=AttemptSplit(success=1, total=1)),
        )


def test_player_match_stats_three_pillars() -> None:
    row = PlayerMatchStats(
        match_id=uuid4(),
        player_id=uuid4(),
        team_id=uuid4(),
        jersey_number=10,
        position="st",
        offensive=OffensiveStats(minutes=12.0),
    )
    assert row.position == "ST"
    assert row.offensive.minutes == 12.0
    assert row.defensive.aerial_duels.total == 0
    assert row.distribution.passes.total == 0
    updated = row.replace_pillars(offensive=row.offensive.record_assist())
    assert updated.offensive.assists == 1
    assert updated.stats_id == row.stats_id


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        OffensiveStats(xg=0.4)  # type: ignore[call-arg]
