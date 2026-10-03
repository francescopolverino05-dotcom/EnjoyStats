"""Fold a match tag sheet into two collective team pillars.

Film tracking cannot honestly name 22 individuals. The Impact-style
analyse result is therefore Home / Away: every tagged event for a side
is run through the same Spiideo four-pillar collector used for players
(offensive, defensive, distribution, possession). Substitutions do not
break this — there are still two teams of eleven for 90 minutes.

Wyscout ``<analysis>`` exports are usually one analysed side. Offensive
tags belong to that side; defensive tags (saves, goals conceded, duels)
are still that side's defending — not a second full team sheet. The
opposition only appears as thin synthetic goals from ``Goal subiti``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from analytics.game_ingest import MatchRundown, event_clock_minutes
from analytics.stats_collector import new_collector
from analytics.team_sheet import team_sheets_from_rundown
from data_models.events import EventType, MatchEvent
from data_models.player_stats import PlayerMatchProfile, PossessionStats

_INVENTED_FILM_NAME = re.compile(r"^(Home|Away)\s+[A-Z]{1,3}\s+\d{1,2}$")


@dataclass(frozen=True)
class SheetPerspective:
    """Which side an uploaded sheet honestly describes.

    One-sided Wyscout analyses are not Home+Away boards. Offensive /
    construction numbers are the analysed team; defensive numbers are
    that same team's defending (opposition pressure inferred from saves
    and goals conceded). Opposition goals on the sheet come only from
    ``Goal subiti`` and can undercount the real score.
    """

    one_sided: bool
    analysed_team_id: UUID | None
    analysed_team_name: str
    opposition_team_id: UUID | None
    opposition_team_name: str
    analysed_goals: int
    opposition_goals_on_sheet: int
    analysed_event_count: int
    opposition_event_count: int

    @property
    def score_line(self) -> str:
        """Human score line that does not overclaim a full scoresheet."""

        if not self.one_sided:
            return (
                f"{self.analysed_team_name} {self.analysed_goals}–"
                f"{self.opposition_goals_on_sheet} {self.opposition_team_name}"
            )
        return (
            f"{self.analysed_team_name} {self.analysed_goals} "
            f"(this sheet) · {self.opposition_team_name} "
            f"{self.opposition_goals_on_sheet} on this sheet only"
        )


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


def _event_team_counts(rundown: MatchRundown) -> dict[UUID, int]:
    counts: dict[UUID, int] = defaultdict(int)
    for event in rundown.events:
        counts[event.team_id] += 1
    return dict(counts)


def is_one_sided_sheet(rundown: MatchRundown) -> bool:
    """Return whether almost every tag belongs to a single team.

    Wyscout ``<analysis>`` exports are usually one analysed side. The other
    team only receives synthetic tags (typically the goal they scored).
    """

    counts = _event_team_counts(rundown)
    if len(counts) < 2:
        return True
    total = sum(counts.values()) or 1
    return min(counts.values()) / total < 0.05


def analysis_perspective(rundown: MatchRundown) -> SheetPerspective:
    """Decide which side the sheet honestly describes, and how complete the score is."""

    sheets = team_sheets_from_rundown(rundown)
    counts = _event_team_counts(rundown)
    home_name = rundown.summary.home_team_name or "Home"
    away_name = rundown.summary.away_team_name or "Away"
    one_sided = is_one_sided_sheet(rundown)

    if not counts:
        return SheetPerspective(
            one_sided=True,
            analysed_team_id=None,
            analysed_team_name=home_name,
            opposition_team_id=None,
            opposition_team_name=away_name,
            analysed_goals=0,
            opposition_goals_on_sheet=0,
            analysed_event_count=0,
            opposition_event_count=0,
        )

    analysed_id = max(counts, key=counts.get)
    opposition_id = next((team_id for team_id in counts if team_id != analysed_id), None)
    name_by_id = {sheet.team_id: sheet.team_name for sheet in sheets}
    analysed_name = name_by_id.get(analysed_id, home_name)
    opposition_name = (
        name_by_id.get(opposition_id, away_name) if opposition_id is not None else away_name
    )
    # Prefer summary labels when sheet names are still Home/Away placeholders.
    if analysed_name in {"Home", "Away"} and home_name not in {"Home", "Away"}:
        # Majority tags are always remapped onto the home UUID for one-team XML.
        analysed_name = home_name
        opposition_name = away_name
    elif opposition_name in {"Home", "Away"} and away_name not in {"Home", "Away"}:
        opposition_name = away_name

    def _goals_for(team_id: UUID | None) -> int:
        if team_id is None:
            return 0
        return sum(
            1
            for event in rundown.events
            if event.team_id == team_id and (event.is_goal or event.event_type is EventType.GOAL)
        )

    return SheetPerspective(
        one_sided=one_sided,
        analysed_team_id=analysed_id,
        analysed_team_name=analysed_name,
        opposition_team_id=opposition_id,
        opposition_team_name=opposition_name,
        analysed_goals=_goals_for(analysed_id),
        opposition_goals_on_sheet=_goals_for(opposition_id),
        analysed_event_count=counts.get(analysed_id, 0),
        opposition_event_count=counts.get(opposition_id, 0) if opposition_id else 0,
    )


def analysed_team_profile(rundown: MatchRundown) -> PlayerMatchProfile | None:
    """Return the collective pillar row for the analysed side, if any."""

    perspective = analysis_perspective(rundown)
    if perspective.analysed_team_id is None:
        return None
    for profile in team_profiles_from_rundown(rundown):
        if profile.team_id == perspective.analysed_team_id:
            return profile
    teams = team_profiles_from_rundown(rundown)
    return teams[0] if teams else None
