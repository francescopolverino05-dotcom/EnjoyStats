"""Spiideo-style match tag sheets that the stat rundown is counted from.

A tag is one on-ball action (pass, shot, recovery, …) with a clock and a
pitch location. EnjoyStats folds the same list through
:func:`~analytics.game_ingest.collect_game`, so the numbers on the dashboard
are the tag sheet — not a second, disconnected tally.

The XML export is a compact Spiideo-like event list: teams, players, tags,
and attack sequences. Drop the ``.tags.xml`` sidecar next to a film, or
upload the XML itself, to re-collect from official / corrected tags.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4, uuid5
from xml.etree.ElementTree import Element, SubElement, fromstring, indent, tostring

from analytics.game_ingest import GamePayload, MatchRundown, PlayerRosterEntry, collect_game
from data_models.events import EventType, MatchEvent, ShotOutcome

TAG_NAMESPACE: UUID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SIDECAR_SUFFIX = ".tags.xml"
_TEAM_SPLIT = re.compile(r"_-_|_vs_|_v_|vs\.?", re.IGNORECASE)
_AWAY_TRIM = re.compile(
    r"(_v\d+|__\d+-\d+_?|_\d+-\d+_?|__?\d+p_?|_540p|_720p|_1080p|_2160p|_excerpt).*$",
    re.IGNORECASE,
)


def infer_team_names(filename: str) -> tuple[str, str]:
    """Read home / away names from a film filename like ``Ascoli_-_Spezia``.

    Returns:
        ``(home, away)``. Generic names when the stem has no separator.
    """

    stem = Path(filename).stem.replace("  ", " ").strip()
    parts = _TEAM_SPLIT.split(stem, maxsplit=1)
    if len(parts) != 2:
        return "Home", "Away"
    home = parts[0].replace("_", " ").strip().title() or "Home"
    away_raw = _AWAY_TRIM.sub("", parts[1])
    away = away_raw.replace("_", " ").strip().title() or "Away"
    return home, away


def sidecar_path_for(video_path: Path) -> Path:
    """Return the ``.tags.xml`` path written next to a collected film."""

    resolved = video_path.expanduser()
    return resolved.with_name(resolved.stem + SIDECAR_SUFFIX)


def attacks_from_events(events: Sequence[MatchEvent]) -> list[dict[str, object]]:
    """Group consecutive same-team tags into attack sequences."""

    attacks: list[dict[str, object]] = []
    current: list[MatchEvent] = []
    for event in events:
        if current and event.team_id != current[-1].team_id:
            attacks.append(_attack_row(current))
            current = [event]
            continue
        current.append(event)
    if current:
        attacks.append(_attack_row(current))
    return attacks


def _attack_row(events: Sequence[MatchEvent]) -> dict[str, object]:
    last = events[-1]
    end_type = last.event_type.value
    if last.is_goal or last.event_type is EventType.GOAL:
        end_type = "goal"
    return {
        "team_id": str(events[0].team_id),
        "start_ms": events[0].video_timestamp_ms,
        "end_ms": last.video_timestamp_ms,
        "tag_count": len(events),
        "end_type": end_type,
        "tag_ids": [str(event.event_id) for event in events],
    }


def rundown_to_xml(rundown: MatchRundown, *, source: str = "enjoystats-auto") -> str:
    """Serialize a collected match as a Spiideo-style tag XML document."""

    root = Element(
        "MatchTags",
        {
            "sport": "football",
            "source": source,
            "match_id": str(rundown.match_id),
            "events": str(rundown.summary.event_count),
            "goals": str(rundown.summary.goals),
            "shots": str(rundown.summary.shots),
            "passes": str(rundown.summary.passes),
        },
    )
    teams_el = SubElement(root, "Teams")
    seen_teams: dict[UUID, str] = {}
    for profile in rundown.players:
        name = profile.player_name or ""
        team_name = name.split(" ", 1)[0] if name else f"Team {str(profile.team_id)[:8]}"
        seen_teams.setdefault(profile.team_id, team_name)
    for team_id, name in seen_teams.items():
        SubElement(teams_el, "Team", {"id": str(team_id), "name": name})

    players_el = SubElement(root, "Players")
    for profile in rundown.players:
        SubElement(
            players_el,
            "Player",
            {
                "id": str(profile.player_id),
                "team_id": str(profile.team_id),
                "name": profile.player_name or "",
                "jersey": str(profile.jersey_number or ""),
                "position": profile.position or "",
            },
        )

    tags_el = SubElement(root, "Tags")
    for event in rundown.events:
        attrs = {
            "id": str(event.event_id),
            "type": event.event_type.value,
            "team_id": str(event.team_id),
            "player_id": "" if event.player_id is None else str(event.player_id),
            "period": str(event.period),
            "minute": str(event.minute),
            "second": str(event.second),
            "timestamp_ms": str(event.video_timestamp_ms),
            "x": f"{event.x:.2f}",
            "y": f"{event.y:.2f}",
            "successful": "true" if event.successful else "false",
            "is_goal": "true" if event.is_goal else "false",
        }
        if event.end_x is not None:
            attrs["end_x"] = f"{event.end_x:.2f}"
        if event.end_y is not None:
            attrs["end_y"] = f"{event.end_y:.2f}"
        if event.shot_outcome is not None:
            attrs["shot_outcome"] = event.shot_outcome.value
        SubElement(tags_el, "Tag", attrs)

    attacks_el = SubElement(root, "Attacks")
    for attack in attacks_from_events(rundown.events):
        SubElement(
            attacks_el,
            "Attack",
            {
                "team_id": str(attack["team_id"]),
                "start_ms": str(attack["start_ms"]),
                "end_ms": str(attack["end_ms"]),
                "tag_count": str(attack["tag_count"]),
                "end_type": str(attack["end_type"]),
                "tag_ids": ",".join(attack["tag_ids"]),  # type: ignore[arg-type]
            },
        )

    indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode")


def write_sidecar_xml(rundown: MatchRundown, video_path: Path) -> Path:
    """Write ``{stem}.tags.xml`` beside the film that produced ``rundown``."""

    destination = sidecar_path_for(video_path)
    destination.write_text(rundown_to_xml(rundown), encoding="utf-8")
    return destination


def parse_tag_xml(raw: str) -> GamePayload:
    """Parse a MatchTags XML document into a collectable game payload."""

    root = fromstring(raw)
    if root.tag != "MatchTags":
        raise ValueError("Tag XML must have a MatchTags root element.")
    match_raw = root.attrib.get("match_id", "").strip()
    match_id = UUID(match_raw) if match_raw else uuid5(TAG_NAMESPACE, raw[:80])

    roster: list[PlayerRosterEntry] = []
    players_el = root.find("Players")
    if players_el is not None:
        for player in players_el.findall("Player"):
            player_id = player.attrib.get("id", "").strip()
            team_id = player.attrib.get("team_id", "").strip()
            if not player_id or not team_id:
                continue
            jersey_raw = player.attrib.get("jersey", "").strip()
            roster.append(
                PlayerRosterEntry(
                    player_id=UUID(player_id),
                    team_id=UUID(team_id),
                    jersey_number=int(jersey_raw) if jersey_raw.isdigit() else None,
                    player_name=player.attrib.get("name", ""),
                    position=player.attrib.get("position", ""),
                )
            )

    events: list[MatchEvent] = []
    tags_el = root.find("Tags")
    if tags_el is None:
        raise ValueError("Tag XML has no Tags to collect.")
    for tag in tags_el.findall("Tag"):
        events.append(_event_from_tag_element(tag, match_id=match_id))
    if not events:
        raise ValueError("Tag XML has no Tags to collect.")
    return GamePayload(match_id=match_id, players=roster, events=events)


def _event_from_tag_element(tag: Element, *, match_id: UUID) -> MatchEvent:
    event_type = EventType(tag.attrib.get("type", "pass"))
    player_raw = tag.attrib.get("player_id", "").strip()
    end_x = tag.attrib.get("end_x")
    end_y = tag.attrib.get("end_y")
    outcome_raw = tag.attrib.get("shot_outcome", "").strip()
    is_goal = tag.attrib.get("is_goal", "false").lower() == "true"
    if event_type is EventType.GOAL:
        is_goal = True
    payload: dict[str, object] = {
        "event_id": UUID(tag.attrib["id"]) if tag.attrib.get("id") else uuid4(),
        "match_id": match_id,
        "team_id": UUID(tag.attrib["team_id"]),
        "player_id": UUID(player_raw) if player_raw else None,
        "period": int(tag.attrib.get("period", "1")),
        "minute": int(tag.attrib.get("minute", "0")),
        "second": int(tag.attrib.get("second", "0")),
        "event_type": event_type,
        "x": float(tag.attrib.get("x", "50")),
        "y": float(tag.attrib.get("y", "50")),
        "successful": tag.attrib.get("successful", "true").lower() != "false",
        "is_goal": is_goal,
        "video_timestamp_ms": int(tag.attrib.get("timestamp_ms", "0")),
    }
    if end_x is not None and end_y is not None:
        payload["end_x"] = float(end_x)
        payload["end_y"] = float(end_y)
    if event_type in {EventType.SHOT, EventType.GOAL}:
        if outcome_raw:
            payload["shot_outcome"] = ShotOutcome(outcome_raw)
        elif is_goal:
            payload["shot_outcome"] = ShotOutcome.ON_TARGET
        else:
            payload["shot_outcome"] = ShotOutcome.MISSED
        if event_type is EventType.GOAL:
            payload["is_goal"] = True
            payload["shot_outcome"] = ShotOutcome.ON_TARGET
    return MatchEvent.model_validate(payload)


def collect_from_tag_xml(raw: str) -> MatchRundown:
    """Collect four-pillar stats from a Spiideo-style tag XML document."""

    return collect_game(parse_tag_xml(raw))


def load_sidecar_xml(video_path: Path) -> MatchRundown | None:
    """Load ``{stem}.tags.xml`` beside a film, or ``None`` when it is missing."""

    sidecar = sidecar_path_for(video_path)
    if not sidecar.is_file():
        return None
    return collect_from_tag_xml(sidecar.read_text(encoding="utf-8"))
