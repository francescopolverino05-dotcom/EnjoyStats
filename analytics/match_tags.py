"""Match tag sheets that the stat rundown is counted from.

A tag is one on-ball action (pass, shot, recovery, …) with a clock and a
pitch location. EnjoyStats folds the same list through
:func:`~analytics.game_ingest.collect_game`, so the numbers on the dashboard
are the tag sheet — not a second, disconnected tally.

Two XML shapes are accepted:

* EnjoyStats / Spiideo ``MatchTags`` (teams, players, tags, attacks).
* Wyscout / Nacsport ``<analysis>`` exports (Italian ``actionName`` labels).

Drop a ``.tags.xml`` sidecar next to a film, or upload the XML itself, to
collect from official or corrected tags.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4, uuid5
from xml.etree.ElementTree import Element, SubElement, fromstring, indent, tostring

from analytics.game_ingest import GamePayload, MatchRundown, PlayerRosterEntry, collect_game
from data_models.events import EventType, MatchEvent, ShotOutcome

TAG_NAMESPACE: UUID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SIDECAR_SUFFIX = ".tags.xml"
_TEAM_SPLIT = re.compile(r"\s+v(?:s\.?)?\s+|_-_|_vs_|_v_|vs\.?", re.IGNORECASE)
_AWAY_TRIM = re.compile(
    r"("
    r"\s*\(\d+\s*-\s*\d+\)"
    r"|_v\d+"
    r"|__\d+-\d+_?"
    r"|_\d+-\d+_?"
    r"|__?\d+p_?"
    r"|_540p|_720p|_1080p|_2160p|_excerpt"
    r").*$",
    re.IGNORECASE,
)
_ACTION_NAME = re.compile(r"^(?:\((?P<jersey>\d+)\)\s*)?(?P<player>.*?)\s*/\s*(?P<kind>.+)$")
_HALF_KICKOFF = {
    "inizio primo tempo": 1,
    "inizio secondo tempo": 2,
    "fine primo tempo": 1,
    "fine secondo tempo": 2,
}
_SKIP_KINDS = {
    "inizio primo tempo",
    "inizio secondo tempo",
    "fine primo tempo",
    "fine secondo tempo",
    "goal_kick",
    "spazzate",
    "palle vaganti",
    "aggressività",
    "aggressivita",
    "accelerazioni",
    "riflessi",
    "uscita",
    "movimento incontro alla palla",
}
_KEEPER_KINDS = {"parate", "goal subiti", "riflessi", "uscita"}
_KIND_TO_EVENT: dict[str, EventType] = {
    "passaggi": EventType.PASS,
    "passaggi filtranti": EventType.PASS,
    "distribuzione palla": EventType.PASS,
    "lanci lunghi": EventType.PASS,
    "cross": EventType.CROSS,
    "tiri": EventType.SHOT,
    "tiro - testa": EventType.SHOT,
    "tiro fuori dallo specchio": EventType.SHOT,
    "occasione da goal": EventType.SHOT,
    "goal subiti": EventType.GOAL_CONCEDED,
    "intercetti palla": EventType.INTERCEPTION,
    "recupero": EventType.BALL_RECOVERY,
    "palle perse": EventType.BALL_LOST,
    "duelli aerei": EventType.AERIAL_DUEL,
    "duelli offensivi": EventType.GROUND_DUEL,
    "duelli difensivi": EventType.GROUND_DUEL,
    "1 contro 1 difesa": EventType.GROUND_DUEL,
    "1 contro 1 e dribbling": EventType.GROUND_DUEL,
    "falli": EventType.FOUL_COMMITTED,
    "falli subiti": EventType.FOUL_WON,
    "rimesse laterali": EventType.THROW_IN,
    "calcio di punizione": EventType.FREE_KICK,
    "calcio d'angolo": EventType.CORNER,
    "fuorigioco": EventType.OFFSIDE,
    "coinvolgimento nell'azione del goal": EventType.ASSIST,
    "parate": EventType.SAVE,
}


def infer_team_names(filename: str) -> tuple[str, str]:
    """Read home / away names from a film or tag-sheet title.

    Accepts broadcast stems (``Ascoli_-_Spezia``) and Wyscout titles
    (``Arsenal v Palace (1-1)``).

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


def find_official_tag_xml(video_path: Path, *directories: Path) -> Path | None:
    """Find a Wyscout / Nacsport ``<analysis>`` sheet that belongs with a film.

    Matches on team names inferred from the filename (``Arsenal_v_Palace``)
    and on a same-folder ``.xml`` whose stem shares the fixture prefix.
    Auto-written ``.tags.xml`` sidecars are ignored so a sparse previous
    collect cannot mask official tags.
    """

    resolved = video_path.expanduser()
    home, away = infer_team_names(resolved.name)
    wanted = {home.casefold(), away.casefold()}
    generic = wanted <= {"home", "away"}
    folders: list[Path] = [resolved.parent, *directories]
    seen: set[Path] = set()
    ranked: list[tuple[int, Path]] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        try:
            listing = list(folder.iterdir())
        except OSError:
            continue
        for candidate in listing:
            suffix = candidate.suffix.lower()
            if suffix != ".xml" or candidate.name.endswith(SIDECAR_SUFFIX):
                continue
            try:
                path = candidate.resolve()
            except OSError:
                continue
            if path in seen or not path.is_file() or path.stat().st_size <= 0:
                continue
            seen.add(path)
            if not _looks_like_analysis_xml(path):
                continue
            sheet_home, sheet_away = infer_team_names(path.name)
            names = {sheet_home.casefold(), sheet_away.casefold()}
            score = 0
            film_prefix = resolved.stem.split("__")[0].casefold()
            xml_prefix = path.stem.split("__")[0].casefold()
            if film_prefix and film_prefix == xml_prefix:
                score += 4
            if not generic and names == wanted:
                score += 3
            left = resolved.stem.casefold()
            right = path.stem.casefold()
            if left in right or right in left:
                score += 2
            if score > 0:
                ranked.append((score, path))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1].name))
    return ranked[0][1]


def _looks_like_analysis_xml(path: Path) -> bool:
    try:
        head = path.read_bytes()[:800].decode("utf-8-sig", errors="replace")
    except OSError:
        return False
    lowered = head.lstrip().lower()
    return lowered.startswith("<analysis") or "<analysis" in lowered[:400]


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


def rundown_to_csv(rundown: MatchRundown) -> str:
    """Serialize the tag sheet as CSV so an analyst can drop it into Excel.

    Same rows as :func:`rundown_to_xml` — one line per tagged action.
    """

    names = {profile.player_id: profile.player_name or "" for profile in rundown.players}
    team_order: list[UUID] = []
    for profile in rundown.players:
        if profile.team_id not in team_order:
            team_order.append(profile.team_id)
    for event in rundown.events:
        if event.team_id not in team_order:
            team_order.append(event.team_id)
    labels = {
        team_id: (
            (rundown.summary.home_team_name or "Home")
            if index == 0
            else (rundown.summary.away_team_name or "Away") if index == 1 else f"Team {index + 1}"
        )
        for index, team_id in enumerate(team_order)
    }
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        (
            "Clock",
            "Period",
            "Minute",
            "Second",
            "Tag",
            "Player",
            "Team",
            "X",
            "Y",
            "End X",
            "End Y",
            "Goal",
            "Successful",
        )
    )
    for event in rundown.events:
        writer.writerow(
            (
                f"{event.period}' {event.minute:02d}:{event.second:02d}",
                event.period,
                event.minute,
                event.second,
                event.event_type.value,
                names.get(event.player_id, "") if event.player_id else "",
                labels.get(event.team_id, "Team"),
                f"{event.x:.2f}",
                f"{event.y:.2f}",
                "" if event.end_x is None else f"{event.end_x:.2f}",
                "" if event.end_y is None else f"{event.end_y:.2f}",
                "true" if event.is_goal else "false",
                "true" if event.successful else "false",
            )
        )
    return buffer.getvalue()


def write_sidecar_xml(rundown: MatchRundown, video_path: Path) -> Path:
    """Write ``{stem}.tags.xml`` beside the film that produced ``rundown``."""

    destination = sidecar_path_for(video_path)
    destination.write_text(rundown_to_xml(rundown), encoding="utf-8")
    return destination


def parse_tag_xml(raw: str) -> GamePayload:
    """Parse a MatchTags or Wyscout/Nacsport analysis XML into a game payload."""

    try:
        root = fromstring(raw)
    except Exception as exc:  # noqa: BLE001 — ElementTree raises ParseError
        raise ValueError(f"Tag XML is not well-formed ({exc}).") from exc
    if root.tag == "analysis":
        return _parse_analysis_xml(root)
    if root.tag != "MatchTags":
        raise ValueError("Tag XML must have a MatchTags or analysis root element.")
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
    """Collect four-pillar stats from a MatchTags or Wyscout analysis XML."""

    return collect_game(parse_tag_xml(raw))


def _majority_team_id(payload: GamePayload) -> UUID:
    counts = Counter(event.team_id for event in payload.events)
    if not counts:
        raise ValueError("Official tag sheet has no events to merge.")
    return counts.most_common(1)[0][0]


def _flip_x(value: float) -> float:
    return round(100.0 - float(value), 4)


def _remap_side(
    payload: GamePayload,
    *,
    match_id: UUID,
    team_id: UUID,
    side: str,
    flip_attack: bool,
) -> tuple[list[PlayerRosterEntry], list[MatchEvent]]:
    """Keep the analysed side of a one-team export and point it at ``team_id``."""

    analysed = _majority_team_id(payload)
    player_map: dict[UUID, UUID] = {}
    roster: list[PlayerRosterEntry] = []
    for entry in payload.players:
        if entry.team_id != analysed:
            continue
        new_id = uuid5(match_id, f"{side}-player:{entry.player_id}")
        player_map[entry.player_id] = new_id
        roster.append(
            PlayerRosterEntry(
                player_id=new_id,
                team_id=team_id,
                jersey_number=entry.jersey_number,
                player_name=entry.player_name,
                position=entry.position,
            )
        )
    events: list[MatchEvent] = []
    for event in payload.events:
        if event.team_id != analysed:
            continue
        player_id = player_map.get(event.player_id)
        if player_id is None and event.player_id is not None:
            player_id = uuid5(match_id, f"{side}-player:{event.player_id}")
        updates: dict[str, object] = {
            "event_id": uuid5(match_id, f"{side}-event:{event.event_id}"),
            "match_id": match_id,
            "team_id": team_id,
            "player_id": player_id,
            "attacking_left_to_right": not flip_attack,
        }
        if flip_attack:
            updates["x"] = _flip_x(event.x)
            if event.end_x is not None:
                updates["end_x"] = _flip_x(event.end_x)
        events.append(event.model_copy(update=updates))
    if not events:
        raise ValueError(f"{side.title()} official sheet has no analysed-side tags.")
    return roster, events


def merge_analysis_payloads(home: GamePayload, away: GamePayload) -> GamePayload:
    """Join two one-team Wyscout analyses into one official two-team payload.

    Each file's majority team is the analysed side. Synthetic opposition
    tags (the thin ``Goal subiti`` goal) are dropped because the other
    file already has that side's real sheet. Away coordinates are flipped
    so both teams attack in the same 0–100 match frame.
    """

    match_id = home.match_id or away.match_id or uuid4()
    home_team = uuid5(match_id, "team:home")
    away_team = uuid5(match_id, "team:away")
    home_roster, home_events = _remap_side(
        home, match_id=match_id, team_id=home_team, side="home", flip_attack=False
    )
    away_roster, away_events = _remap_side(
        away, match_id=match_id, team_id=away_team, side="away", flip_attack=True
    )
    return GamePayload(
        match_id=match_id,
        players=home_roster + away_roster,
        events=home_events + away_events,
        home_team_name=home.home_team_name or "Home",
        away_team_name=home.away_team_name or away.home_team_name or "Away",
    )


def collect_paired_analysis(home_xml: str, away_xml: str) -> MatchRundown:
    """Collect one rundown from Home + Away official one-team analysis XMLs."""

    return collect_game(merge_analysis_payloads(parse_tag_xml(home_xml), parse_tag_xml(away_xml)))


def _unescape_label(raw: str) -> str:
    """Turn Nacsport ``O''Neill`` escaping into a display name."""

    return raw.replace("''", "'").replace("  ", " ").strip()


def _clock_seconds(raw: str) -> int:
    """Parse ``HH:MM:SS`` or ``MM:SS`` into seconds from video start."""

    parts = [piece for piece in raw.strip().split(":") if piece != ""]
    if not parts:
        return 0
    try:
        numbers = [int(piece) for piece in parts]
    except ValueError:
        return 0
    if len(numbers) == 1:
        return max(0, numbers[0])
    if len(numbers) == 2:
        minutes, seconds = numbers
        return max(0, minutes * 60 + seconds)
    hours, minutes, seconds = numbers[-3], numbers[-2], numbers[-1]
    return max(0, hours * 3600 + minutes * 60 + seconds)


def _period_clock(total_seconds: int, *, second_half_start_s: int) -> tuple[int, int, int]:
    """Map a video clock onto regulation period / minute / second."""

    period = 2 if total_seconds >= second_half_start_s else 1
    if period == 2:
        remaining = max(0, total_seconds - 45 * 60)
    else:
        remaining = total_seconds
    minute = min(150, remaining // 60)
    second = remaining % 60
    return period, minute, second


def _split_action_name(action_name: str) -> tuple[int | None, str, str]:
    """Read ``(4) M. Salmon / Passaggi`` into jersey, player, kind."""

    match = _ACTION_NAME.match(action_name.strip())
    if match is None:
        return None, "", _unescape_label(action_name)
    jersey_raw = match.group("jersey")
    jersey = int(jersey_raw) if jersey_raw and jersey_raw.isdigit() else None
    if jersey is not None and not 1 <= jersey <= 99:
        jersey = None
    player = _unescape_label(match.group("player") or "")
    kind = _unescape_label(match.group("kind") or "")
    return jersey, player, kind


def _normalize_kind(kind: str) -> str:
    return " ".join(kind.strip().lower().replace("’", "'").split())


def _event_type_for_kind(kind: str) -> EventType | None:
    normalized = _normalize_kind(kind)
    if normalized in _SKIP_KINDS or normalized in _HALF_KICKOFF:
        return None
    if normalized.startswith("goal di") or normalized == "goal":
        return EventType.GOAL
    return _KIND_TO_EVENT.get(normalized)


def _analysis_coords(
    event_type: EventType,
    *,
    kind: str,
) -> tuple[float, float, float | None, float | None]:
    """Default 0–100 points when the Nacsport field mapper is empty."""

    normalized = _normalize_kind(kind)
    if event_type is EventType.GOAL:
        return 88.0, 50.0, None, None
    if event_type is EventType.GOAL_CONCEDED:
        return 8.0, 50.0, None, None
    if event_type is EventType.SHOT:
        return 82.0, 50.0, None, None
    if event_type is EventType.CROSS:
        return 75.0, 22.0, 92.0, 18.0
    if event_type is EventType.ASSIST:
        return 70.0, 50.0, 88.0, 50.0
    if event_type is EventType.CORNER:
        return 99.0, 5.0, 90.0, 50.0
    if "lanci" in normalized:
        return 38.0, 50.0, 72.0, 50.0
    if "filtranti" in normalized:
        return 55.0, 50.0, 82.0, 48.0
    if event_type in {
        EventType.PASS,
        EventType.THROW_IN,
        EventType.FREE_KICK,
    }:
        return 50.0, 50.0, 58.0, 50.0
    return 50.0, 50.0, None, None


def _parse_analysis_xml(root: Element) -> GamePayload:
    """Turn a Wyscout / Nacsport ``<analysis>`` export into tagged events.

    These sheets are usually one-team (the analysed side). ``Goal subiti`` is
    a goal conceded by that side, so a synthetic opposition ``GOAL`` is added
    and the match headline stays 1–1 when the official tags say so.
    """

    analysis_id = root.attrib.get("id", "").strip()
    try:
        match_id = UUID(analysis_id) if analysis_id else uuid4()
    except ValueError:
        match_id = uuid5(TAG_NAMESPACE, analysis_id or "analysis")
    title = root.attrib.get("title", "") or Path(root.attrib.get("videoFilepath", "")).name
    home_name, away_name = infer_team_names(title)
    home_team = uuid5(match_id, "team:home")
    away_team = uuid5(match_id, "team:away")

    actions_el = root.find("actions")
    actions = list(actions_el) if actions_el is not None else []
    second_half_start_s = 45 * 60
    keeper_keys: set[tuple[int | None, str]] = set()
    parsed_rows: list[tuple[Element, int | None, str, str, int]] = []
    for action in actions:
        action_name = action.attrib.get("actionName", "")
        jersey, player, kind = _split_action_name(action_name)
        clock_s = _clock_seconds(action.attrib.get("startTime", "00:00:00"))
        parsed_rows.append((action, jersey, player, kind, clock_s))
        normalized = _normalize_kind(kind)
        if normalized == "inizio secondo tempo":
            second_half_start_s = clock_s
        if normalized in _KEEPER_KINDS and player:
            keeper_keys.add((jersey, player.casefold()))

    roster: dict[UUID, PlayerRosterEntry] = {}
    events: list[MatchEvent] = []
    opposition_scorer_id: UUID | None = None

    for action, jersey, player, kind, clock_s in parsed_rows:
        event_type = _event_type_for_kind(kind)
        if event_type is None:
            continue
        if (
            event_type is EventType.SHOT
            and _normalize_kind(kind) == "tiro fuori dallo specchio"
            and (jersey, player.casefold()) in keeper_keys
        ):
            continue
        if not player:
            continue
        player_id = uuid5(match_id, f"player:{jersey or 0}:{player.casefold()}")
        period, minute, second = _period_clock(clock_s, second_half_start_s=second_half_start_s)
        start_x, start_y, end_x, end_y = _analysis_coords(event_type, kind=kind)
        action_id = action.attrib.get("id", "").strip()
        try:
            event_id = UUID(action_id) if action_id else uuid4()
        except ValueError:
            event_id = uuid5(match_id, action_id or kind)
        is_goal = event_type is EventType.GOAL
        successful = event_type not in {
            EventType.BALL_LOST,
            EventType.OFFSIDE,
            EventType.GOAL_CONCEDED,
        }
        if event_type is EventType.SHOT and _normalize_kind(kind) == "tiro fuori dallo specchio":
            successful = False
        if event_type is EventType.GROUND_DUEL and _normalize_kind(kind) in {
            "duelli difensivi",
            "1 contro 1 difesa",
        }:
            successful = True
        payload: dict[str, object] = {
            "event_id": event_id,
            "match_id": match_id,
            "team_id": home_team,
            "player_id": player_id,
            "period": period,
            "minute": minute,
            "second": second,
            "event_type": event_type,
            "x": start_x,
            "y": start_y,
            "successful": successful,
            "is_goal": is_goal,
            "is_progressive": (
                "filtranti" in _normalize_kind(kind) or "lanci" in _normalize_kind(kind)
            ),
            "video_timestamp_ms": clock_s * 1000,
        }
        if end_x is not None and end_y is not None:
            payload["end_x"] = end_x
            payload["end_y"] = end_y
        if event_type in {EventType.SHOT, EventType.GOAL}:
            if is_goal:
                payload["shot_outcome"] = ShotOutcome.ON_TARGET
                payload["is_goal"] = True
            elif _normalize_kind(kind) == "tiro fuori dallo specchio":
                payload["shot_outcome"] = ShotOutcome.MISSED
            else:
                payload["shot_outcome"] = ShotOutcome.ON_TARGET
        events.append(MatchEvent.model_validate(payload))
        position = "GK" if (jersey, player.casefold()) in keeper_keys else ""
        roster.setdefault(
            player_id,
            PlayerRosterEntry(
                player_id=player_id,
                team_id=home_team,
                jersey_number=jersey,
                player_name=player,
                position=position,
            ),
        )

        if event_type is EventType.GOAL_CONCEDED:
            if opposition_scorer_id is None:
                opposition_scorer_id = uuid5(match_id, "player:opposition-scorer")
                roster[opposition_scorer_id] = PlayerRosterEntry(
                    player_id=opposition_scorer_id,
                    team_id=away_team,
                    player_name=f"{away_name} Scorer",
                )
            events.append(
                MatchEvent.model_validate(
                    {
                        "event_id": uuid5(event_id, "opposition-goal"),
                        "match_id": match_id,
                        "team_id": away_team,
                        "player_id": opposition_scorer_id,
                        "period": period,
                        "minute": minute,
                        "second": second,
                        "event_type": EventType.GOAL,
                        "x": 88.0,
                        "y": 50.0,
                        "successful": True,
                        "is_goal": True,
                        "shot_outcome": ShotOutcome.ON_TARGET,
                        "video_timestamp_ms": clock_s * 1000,
                    }
                )
            )

    if not events:
        raise ValueError("Analysis XML has no mapped on-ball actions to collect.")
    return GamePayload(
        match_id=match_id,
        players=list(roster.values()),
        events=events,
        home_team_name=home_name,
        away_team_name=away_name,
    )


def load_sidecar_xml(video_path: Path) -> MatchRundown | None:
    """Load ``{stem}.tags.xml`` beside a film, or ``None`` when it is missing."""

    sidecar = sidecar_path_for(video_path)
    if not sidecar.is_file():
        return None
    return collect_from_tag_xml(sidecar.read_text(encoding="utf-8"))
