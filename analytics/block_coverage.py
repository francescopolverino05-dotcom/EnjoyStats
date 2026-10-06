"""5-minute block coverage + re-pass helpers (Step C).

Mirrors ``packages/core`` ``BLOCK_SECONDS`` / ``BLOCK_MIN_EVENTS`` so film
collect can flag thin windows and watch them again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from analytics.game_ingest import event_clock_minutes
from data_models.events import MatchEvent

BLOCK_SECONDS = 5 * 60
"""One tagging window = 5 minutes of match clock."""

# ~7.5 actions/min (Wyscout Arsenal–Palace density) → ~38 tags per 5 minutes.
# Blocks under this count are watched again (and again if still thin).
BLOCK_MIN_EVENTS = 30
"""Blocks with fewer events than this are marked for re-pass."""

MAX_REPASS_ROUNDS = 2
"""How many extra denser watches a thin block gets before we stop."""


@dataclass(frozen=True, slots=True)
class BlockCoverage:
    """Coverage row for one 5-minute window."""

    block_index: int
    start_seconds: int
    end_seconds: int
    event_count: int
    home_events: int
    away_events: int
    needs_repass: bool
    processed: bool = True


def block_index_for_clock(clock_seconds: float) -> int:
    """0-based block index for a match-clock second."""

    return max(0, int(clock_seconds // BLOCK_SECONDS))


def expected_block_count(duration_minutes: float) -> int:
    """How many 5-minute blocks a match of ``duration_minutes`` needs."""

    seconds = max(1, int(round(float(duration_minutes) * 60.0)))
    return max(1, (seconds + BLOCK_SECONDS - 1) // BLOCK_SECONDS)


def event_clock_seconds(event: MatchEvent) -> float:
    """Match clock in seconds for a tagged event."""

    return float(event_clock_minutes(event) * 60.0)


def build_coverage_report(
    events: Sequence[MatchEvent],
    duration_minutes: float,
    *,
    home_team_ids: set | None = None,
    min_events: int = BLOCK_MIN_EVENTS,
) -> list[BlockCoverage]:
    """Build a coverage table; sparse blocks get ``needs_repass=True``."""

    count = expected_block_count(duration_minutes)
    buckets: list[list[MatchEvent]] = [[] for _ in range(count)]
    for event in events:
        index = block_index_for_clock(event_clock_seconds(event))
        if index >= count:
            index = count - 1
        buckets[index].append(event)

    rows: list[BlockCoverage] = []
    for index, batch in enumerate(buckets):
        start = index * BLOCK_SECONDS
        end = start + BLOCK_SECONDS
        if home_team_ids is None:
            home_n = 0
            away_n = 0
        else:
            home_n = sum(1 for event in batch if event.team_id in home_team_ids)
            away_n = len(batch) - home_n
        total = len(batch)
        rows.append(
            BlockCoverage(
                block_index=index,
                start_seconds=start,
                end_seconds=end,
                event_count=total,
                home_events=home_n,
                away_events=away_n,
                needs_repass=total < min_events,
                processed=True,
            )
        )
    return rows


def blocks_needing_repass(coverage: Sequence[BlockCoverage]) -> list[int]:
    """Indexes of blocks that should be watched again."""

    return [row.block_index for row in coverage if row.needs_repass]


def filter_events_outside_block(
    events: Sequence[MatchEvent],
    block_index: int,
) -> list[MatchEvent]:
    """Keep every event that does **not** belong to ``block_index``."""

    return [
        event
        for event in events
        if block_index_for_clock(event_clock_seconds(event)) != block_index
    ]


def coverage_as_dicts(coverage: Sequence[BlockCoverage]) -> list[dict[str, object]]:
    """JSON-friendly rows for Streamlit / job status."""

    return [
        {
            "block": row.block_index + 1,
            "start_min": row.start_seconds // 60,
            "end_min": row.end_seconds // 60,
            "events": row.event_count,
            "home": row.home_events,
            "away": row.away_events,
            "status": "re-pass" if row.needs_repass else "ok",
        }
        for row in coverage
    ]
