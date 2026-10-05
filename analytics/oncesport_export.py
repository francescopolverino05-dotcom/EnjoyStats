"""Independent Once Sport Analyser Home/Away XML export (no Grokbot).

Maps collected match events onto Francesco's exact button labels and writes
one ``<analysis>`` XML per team — the same shape StatMan returns, produced
entirely in-repo from film CV or imported tags.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID

from analytics.game_ingest import MatchRundown, event_clock_minutes
from data_models.events import EventType, MatchEvent
from data_models.player_stats import PlayerMatchProfile

# Exact Once Sport button labels — never rename on export.
HOME_BUTTONS: dict[str, str] = {
    "pass": "Passaggi",
    "progressive_pass": "Passaggi filtranti",
    "long_ball": "Lanci lunghi",
    "cross": "Cross",
    "shot": "Tiri",
    "save": "Parate portiere",
    "corner": "Calcio d'angolo",
    "free_kick": "Calcio di punizione",
    "throw_in": "Rimesse laterali",
    "offside": "Fuori gioco",
    "foul": "Falli",
    "foul_won": "Falli subiti",
    "aerial_duel": "Duelli aeree",
    "ground_duel_def": "Duelli difensivi",
    "ground_duel_off": "Duelli offensivi",
    "interception": "Palle intercettate",
    "recovery_high": "Recupero palla nel blocco alto",
    "ball_lost": "Palle perse",
}

AWAY_BUTTONS: dict[str, str] = {
    "pass": "passaggi",
    "progressive_pass": "Passaggi filtranti",
    "long_ball": "Lanci lunghi",
    "cross": "Cross",
    "shot": "Tiri",
    "save": "Parate portiere",
    "corner": "Calcio d'angolo",
    "free_kick": "Calcio di punizione",
    "throw_in": "Rimessa laterale",
    "foul": "falli",
    "foul_won": "Falli subiti",
    "aerial_duel": "Duelli aerei",
    "ground_duel_def": "Duelli difensive",
    "ground_duel_off": "Duelli offensivi",
    "recovery_high": "Recupero palla nel blocco alto",
    "ball_lost": "Palle perse",
}

SHARED_BUTTONS: tuple[str, ...] = ("Inizio tempo", "Fine tempo", "Sostituzione")


def _team_ids(rundown: MatchRundown) -> tuple[UUID | None, UUID | None]:
    """Infer (home, away) team UUIDs from names, then profile/event order."""

    home_name = (rundown.summary.home_team_name or "Home").strip().lower()
    away_name = (rundown.summary.away_team_name or "Away").strip().lower()
    by_name: dict[UUID, str] = {}
    for profile in rundown.players:
        label = (profile.player_name or "").strip().lower()
        if profile.team_id in by_name:
            continue
        if label.startswith(home_name):
            by_name[profile.team_id] = "home"
        elif label.startswith(away_name):
            by_name[profile.team_id] = "away"

    home = next((tid for tid, side in by_name.items() if side == "home"), None)
    away = next((tid for tid, side in by_name.items() if side == "away"), None)
    if home is not None and away is not None:
        return home, away

    order: list[UUID] = []
    for profile in rundown.players:
        if profile.team_id not in order:
            order.append(profile.team_id)
    for event in rundown.events:
        if event.team_id not in order:
            order.append(event.team_id)
    if home is None and order:
        home = order[0]
    if away is None and len(order) > 1:
        away = next((tid for tid in order if tid != home), None)
    return home, away


def _player_index(rundown: MatchRundown) -> dict[UUID, PlayerMatchProfile]:
    return {profile.player_id: profile for profile in rundown.players}


def _clock_hms(event: MatchEvent) -> str:
    total = int(round(event_clock_minutes(event) * 60.0))
    hours, rem = divmod(max(0, total), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _player_label(event: MatchEvent, players: Mapping[UUID, PlayerMatchProfile]) -> str:
    if event.player_id is None:
        return "Unknown"
    profile = players.get(event.player_id)
    if profile is None:
        return "Unknown"
    name = (profile.player_name or "Player").strip() or "Player"
    if profile.jersey_number is not None:
        return f"({profile.jersey_number}) {name}"
    return name


def _pass_kind(event: MatchEvent) -> str:
    if event.event_type is EventType.CROSS:
        return "cross"
    if event.event_type is EventType.CUTBACK:
        return "cross"
    if event.is_progressive:
        return "progressive_pass"
    if event.end_x is not None:
        travel = abs(event.end_x - event.x)
        if travel >= 30.0:
            return "long_ball"
    return "pass"


def _duel_kind(event: MatchEvent, *, home: bool) -> str:
    # Attacking half of the pitch for this side → offensive duel button.
    attacking_right = event.attacking_left_to_right
    attack_x = event.x if attacking_right else 100.0 - event.x
    if attack_x >= 50.0:
        return "ground_duel_off"
    return "ground_duel_def"


def once_sport_button_for(
    event: MatchEvent,
    *,
    side: str,
) -> str | None:
    """Return the exact Once Sport button label for ``event``, or None to skip."""

    buttons = HOME_BUTTONS if side == "home" else AWAY_BUTTONS
    kind = event.event_type

    if kind is EventType.PASS or kind is EventType.ASSIST:
        key = _pass_kind(event)
    elif kind is EventType.CROSS or kind is EventType.CUTBACK:
        key = "cross"
    elif kind is EventType.SHOT or kind is EventType.GOAL:
        key = "shot"
    elif kind is EventType.SAVE:
        key = "save"
    elif kind is EventType.CORNER:
        key = "corner"
    elif kind is EventType.FREE_KICK:
        key = "free_kick"
    elif kind is EventType.THROW_IN:
        key = "throw_in"
    elif kind is EventType.OFFSIDE:
        key = "offside"
    elif kind is EventType.FOUL_COMMITTED:
        key = "foul"
    elif kind is EventType.FOUL_WON:
        key = "foul_won"
    elif kind is EventType.AERIAL_DUEL:
        key = "aerial_duel"
    elif kind is EventType.GROUND_DUEL:
        key = _duel_kind(event, home=side == "home")
    elif kind is EventType.INTERCEPTION:
        key = "interception" if side == "home" else "recovery_high"
    elif kind is EventType.BALL_RECOVERY:
        key = "recovery_high"
    elif kind is EventType.BALL_LOST:
        key = "ball_lost"
    elif kind in {
        EventType.BLOCK_SHOT,
        EventType.BLOCK_CROSS,
        EventType.BLOCK_PASS,
        EventType.YELLOW_CARD,
        EventType.RED_CARD,
        EventType.GOAL_CONCEDED,
    }:
        return None
    else:
        return None

    return buttons.get(key)


def export_oncesport_xml(
    rundown: MatchRundown,
    *,
    side: str,
    video_path: str = "",
) -> str:
    """Serialize one team's events as Once Sport ``<analysis>`` XML."""

    if side not in {"home", "away"}:
        raise ValueError("side must be 'home' or 'away'")
    home_id, away_id = _team_ids(rundown)
    team_id = home_id if side == "home" else away_id
    team_name = (
        rundown.summary.home_team_name if side == "home" else rundown.summary.away_team_name
    ) or ("Home" if side == "home" else "Away")
    opp_name = (
        rundown.summary.away_team_name if side == "home" else rundown.summary.home_team_name
    ) or ("Away" if side == "home" else "Home")
    players = _player_index(rundown)
    title = f"{rundown.summary.home_team_name} v {rundown.summary.away_team_name}"
    match_key = str(rundown.match_id)

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<analysis",
        f'  id="{html.escape(match_key, quote=True)}-{side}"',
        f'  title="{html.escape(title, quote=True)}"',
        '  type="0"',
        f'  videoFilepath="{html.escape(video_path, quote=True)}"',
        f'  analysedTeam="{html.escape(team_name, quote=True)}"',
        f'  oppositionTeam="{html.escape(opp_name, quote=True)}"',
        ">",
        "  <info />",
        "  <actions>",
    ]

    if team_id is None:
        lines.extend(["  </actions>", "</analysis>", ""])
        return "\n".join(lines)

    side_events = [event for event in rundown.events if event.team_id == team_id]
    for event in side_events:
        button = once_sport_button_for(event, side=side)
        if not button:
            continue
        actor = _player_label(event, players)
        action_name = f"{actor} / {button}"
        start = _clock_hms(event)
        # Two-second window matches OnceSport-style instance length.
        end_minutes = event_clock_minutes(event) + (2.0 / 60.0)
        end_total = int(round(end_minutes * 60.0))
        eh, er = divmod(max(0, end_total), 3600)
        em, es = divmod(er, 60)
        end = f"{eh:02d}:{em:02d}:{es:02d}"
        field = html.escape(
            f'[{{"x":{round(event.x, 1)},"y":{round(event.y, 1)}}}]',
            quote=True,
        )
        lines.extend(
            [
                "    <action",
                f'      id="{html.escape(str(event.event_id), quote=True)}"',
                f'      actionName="{html.escape(action_name, quote=True)}"',
                '      actionType="Other"',
                f'      startTime="{start}"',
                f'      endTime="{end}"',
                f'      fieldMapper="{field}"',
                "    />",
            ]
        )

    lines.extend(["  </actions>", "</analysis>", ""])
    return "\n".join(lines)


def export_both_oncesport_xml(
    rundown: MatchRundown,
    *,
    video_path: str = "",
) -> dict[str, str]:
    """Return ``{"home": ..., "away": ...}`` Once Sport analysis XMLs."""

    return {
        "home": export_oncesport_xml(rundown, side="home", video_path=video_path),
        "away": export_oncesport_xml(rundown, side="away", video_path=video_path),
    }


def write_oncesport_pair(
    rundown: MatchRundown,
    destination_dir: Path,
    *,
    stem: str | None = None,
    video_path: str = "",
) -> tuple[Path, Path]:
    """Write ``{stem}_Home.xml`` and ``{stem}_Away.xml`` next to the film."""

    destination_dir = destination_dir.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    base = stem or "match"
    pair = export_both_oncesport_xml(rundown, video_path=video_path)
    home_path = destination_dir / f"{base}_Home.xml"
    away_path = destination_dir / f"{base}_Away.xml"
    home_path.write_text(pair["home"], encoding="utf-8")
    away_path.write_text(pair["away"], encoding="utf-8")
    return home_path, away_path


def home_button_names() -> Sequence[str]:
    """Ordered Home / Attacking (green) Once Sport labels."""

    return tuple(HOME_BUTTONS.values())


def away_button_names() -> Sequence[str]:
    """Ordered Away / Defending (orange) Once Sport labels."""

    return tuple(AWAY_BUTTONS.values())
