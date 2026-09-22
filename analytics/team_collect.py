"""Fold a match tag sheet into two collective team pillars.

Film tracking cannot honestly name 22 individuals. The Impact-style
analyse result is therefore Home / Away: every tagged event for a side
is run through the same Spiideo four-pillar collector used for players
(offensive, defensive, distribution, possession). Substitutions do not
break this — there are still two teams of eleven for 90 minutes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from uuid import UUID

from analytics.game_ingest import MatchRundown, event_clock_minutes
from analytics.stats_collector import new_collector
from analytics.team_sheet import team_sheets_from_rundown
from data_models.events import MatchEvent
from data_models.player_stats import PlayerMatchProfile, PossessionStats

_INVENTED_FILM_NAME = re.compile(r"^(Home|Away)\s+[A-Z]{1,3}\s+\d{1,2}$")


def team_profiles_from_rundown(rundown: MatchRundown) -> list[PlayerMatchProfile]:
    """Build one four-pillar row per team from the match tags."""

    grouped: dict[UUID, list[MatchEvent]] = defaultdict(list)
    for event in rundown.events:
        grouped[event.team_id].append(event)
    sheets = team_sheets_from_rundown(rundown)
    names = {sheet.team_id: sheet.team_name for sheet in sheets}
    order = [sheet.team_id for sheet in sheets]
    if not order:
        order = list(grouped.keys())
    total = max(len(rundown.events), 1)
    match_minutes = max(
        (event_clock_minutes(event) for event in rundown.events),
        default=max(rundown.summary.duration_minutes, 0.1),
    )
    if match_minutes <= 0:
        match_minutes = 0.1
    profiles: list[PlayerMatchProfile] = []
    for team_id in order:
        events = grouped.get(team_id, [])
        name = names.get(team_id, "Team")
        collector = new_collector(
            match_id=rundown.match_id,
            player_id=team_id,
            team_id=team_id,
            jersey_number=None,
            position="TEAM",
            player_name=name[:80],
        )
        for event in events:
            collector.apply(event.model_copy(update={"player_id": team_id}))
        profile = PlayerMatchProfile.from_stats(collector.stats)
        share = len(events) / total
        duration = min(150.0, max(rundown.summary.duration_minutes, match_minutes))
        profile = PlayerMatchProfile.from_stats(
            profile.replace_pillars(
                offensive=profile.offensive.with_minutes(duration),
                possession=PossessionStats(
                    time_minutes=min(duration, share * duration),
                    percentage=round(share * 100.0, 1),
                ),
            )
        )
        profiles.append(profile)
    return profiles


def is_invented_film_identity(profile: PlayerMatchProfile) -> bool:
    """Return whether ``profile`` is a nameless Home/Away blob from film CV."""

    name = (profile.player_name or "").strip()
    return bool(_INVENTED_FILM_NAME.fullmatch(name))


def named_player_profiles(rundown: MatchRundown) -> list[PlayerMatchProfile]:
    """Official-tag players only — hide invented ``Home CM 4`` film rows."""

    return [row for row in rundown.players if not is_invented_film_identity(row)]
