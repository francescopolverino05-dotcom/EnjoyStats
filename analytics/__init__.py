"""StatMan analytics package — film collect, ingest, and StatMan IQ.

Heavy film/CV imports are lazy so ``python -m analytics.collect_worker`` can
start on Railway without loading YOLO/OpenCV at process boot.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "PlayerStatsCollector",
    "collect_from_tag_xml",
    "collect_from_video",
    "collect_game",
    "rundown_to_csv",
    "rundown_to_xml",
    "detect_pass_strings",
    "map_to_play_zone",
    "new_collector",
    "passes_per_5_minute_period",
    "possession_pct_per_15_minute_segment",
    "spatial_breakdown",
    "temporal_breakdown",
]


def __getattr__(name: str) -> Any:
    if name == "collect_game":
        from analytics.game_ingest import collect_game as _collect_game

        return _collect_game
    if name == "PlayerStatsCollector":
        from analytics.stats_collector import PlayerStatsCollector as _PlayerStatsCollector

        return _PlayerStatsCollector
    if name == "new_collector":
        from analytics.stats_collector import new_collector as _new_collector

        return _new_collector
    if name in {"map_to_play_zone", "spatial_breakdown"}:
        from analytics import spatial_zones as _spatial

        return getattr(_spatial, name)
    if name in {
        "detect_pass_strings",
        "passes_per_5_minute_period",
        "possession_pct_per_15_minute_segment",
        "temporal_breakdown",
    }:
        from analytics import temporal_stats as _temporal

        return getattr(_temporal, name)
    if name in {"collect_from_tag_xml", "rundown_to_csv", "rundown_to_xml"}:
        from analytics import match_tags as _tags

        return getattr(_tags, name)
    if name == "collect_from_video":
        from analytics.video_auto_collect import collect_from_video as _collect_from_video

        return _collect_from_video
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
