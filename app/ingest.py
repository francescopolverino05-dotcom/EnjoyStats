"""Dashboard helpers for uploading a match film and showing the rundown."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from analytics.game_ingest import (
    GamePayload,
    MatchRundown,
    collect_game,
    parse_game_payload,
)
from analytics.sample_game import sample_game_payload
from analytics.match_tags import (
    collect_from_tag_xml,
    collect_paired_analysis,
)
from analytics.video_auto_collect import (
    MAX_VIDEO_BYTES,
    VIDEO_SUFFIXES,
    ProgressFn,
    VideoCollectError,
    collect_from_video,
    film_inbox_dir,
    film_upload_dir,
    list_ready_films,
    normalize_film_path,
    write_film_chunks,
)
from app.client import DEFAULT_BASE_URL, REQUEST_TIMEOUT_S, ProfileLoad, probe_api
from app.dummy_data import PitchAction
from app.metrics import directions_from_distribution
from data_models.events import EventType, MatchEvent
from data_models.player_stats import PlayerMatchProfile

INGEST_TIMEOUT_S = 30.0
UPLOAD_DISCONNECT_HINT = (
    "Upload the match film with the chunked uploader on this page "
    "(it retries drops). Then click Analyse Stats."
)


def ready_films() -> list[Path]:
    """Create inbox/uploads folders and list films already on disk."""

    inbox = film_inbox_dir()
    uploads = film_upload_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    uploads.mkdir(parents=True, exist_ok=True)
    return list_ready_films(inbox, uploads)


def ready_match_films() -> list[Path]:
    """Newest-first match videos only (skip tag XML sitting in the inbox)."""

    return [path for path in ready_films() if path.suffix.lower() in VIDEO_SUFFIXES]


def latest_ready_film() -> Path | None:
    """The newest match film on disk, or None when the inbox is empty."""

    films = ready_match_films()
    return films[0] if films else None


def actions_from_team(
    events: Sequence[MatchEvent],
    team_id: UUID,
) -> tuple[PitchAction, ...]:
    """Project every tagged event for one team onto the 2D pitch."""

    actions: list[PitchAction] = []
    for event in events:
        if event.team_id != team_id:
            continue
        kind = event.event_type.value
        if kind not in {"pass", "cross", "cutback", "assist", "shot", "goal"}:
            continue
        actions.append(
            PitchAction(
                event_type=kind,
                x=event.x,
                y=event.y,
                end_x=event.end_x,
                end_y=event.end_y,
                successful=event.successful,
                is_goal=event.is_goal or event.event_type is EventType.GOAL,
                shot_outcome=event.shot_outcome.value if event.shot_outcome else None,
            )
        )
    return tuple(actions)


def actions_from_events(
    events: Sequence[MatchEvent],
    player_id: UUID,
) -> tuple[PitchAction, ...]:
    """Project a player's tagged events onto the 2D pitch plot."""

    actions: list[PitchAction] = []
    for event in events:
        if event.player_id != player_id:
            continue
        kind = event.event_type.value
        if kind not in {"pass", "cross", "cutback", "assist", "shot", "goal"}:
            continue
        actions.append(
            PitchAction(
                event_type=kind,
                x=event.x,
                y=event.y,
                end_x=event.end_x,
                end_y=event.end_y,
                successful=event.successful,
                is_goal=event.is_goal or event.event_type is EventType.GOAL,
                shot_outcome=event.shot_outcome.value if event.shot_outcome else None,
            )
        )
    return tuple(actions)


def load_from_rundown(rundown: MatchRundown, player_id: UUID) -> ProfileLoad:
    """Build the dashboard view model from a collected match."""

    profile = next((row for row in rundown.players if row.player_id == player_id), None)
    if profile is None:
        raise ValueError(f"Player {player_id} is not in this collected match.")
    return ProfileLoad(
        profile=profile,
        directions=directions_from_distribution(profile.distribution),
        source="collected",
        message=(
            f"Auto-collected {rundown.summary.event_count} events "
            f"into a {rundown.summary.player_count}-player rundown."
        ),
        api_online=True,
        actions=actions_from_events(rundown.events, player_id),
    )


def load_from_team_profile(rundown: MatchRundown, profile: PlayerMatchProfile) -> ProfileLoad:
    """Build the dashboard view from a collective Home / Away pillar row.

    Team rows are folded from the tag sheet and are not looked up in
    ``rundown.players`` (film collection invents individual names).
    """

    return ProfileLoad(
        profile=profile,
        directions=directions_from_distribution(profile.distribution),
        source="collected",
        message=(
            f"Collective {profile.player_name} sheet from "
            f"{rundown.summary.event_count} tagged events."
        ),
        api_online=True,
        actions=actions_from_team(rundown.events, profile.team_id),
    )


def collect_sample_match() -> MatchRundown:
    """Collect the bundled sample game without an uploaded file."""

    return collect_game(sample_game_payload())


def collect_official_two_team(
    home_bytes: bytes,
    away_bytes: bytes | None = None,
) -> MatchRundown:
    """Collect an official two-team sheet.

    One file is a two-team export (JSON, MatchTags, or a two-club Wyscout
    sheet). Two files are Home + Away one-team analysis XMLs that are
    merged so both sides keep their real tags.
    """

    home_text = _decode_tag_bytes(home_bytes)
    if away_bytes is None:
        return collect_uploaded_bytes(home_bytes)
    away_text = _decode_tag_bytes(away_bytes)
    try:
        return collect_paired_analysis(home_text, away_text)
    except ValueError as exc:
        raise ValueError(f"Official Home + Away sheets could not be merged ({exc}).") from exc


def _decode_tag_bytes(raw_bytes: bytes) -> str:
    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Tag file is not valid UTF-8 ({exc}).") from exc


def collect_uploaded_bytes(raw_bytes: bytes) -> MatchRundown:
    """Parse an uploaded JSON or tag XML file and collect player stats."""

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Game file is not valid UTF-8 ({exc}).") from exc
    stripped = text.lstrip()
    if stripped.startswith("<"):
        try:
            return collect_from_tag_xml(stripped)
        except ValueError as exc:
            raise ValueError(f"Tag XML could not be collected ({exc}).") from exc
    try:
        decoded: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Game file is not valid JSON ({exc}).") from exc
    return collect_game(parse_game_payload(decoded))


def save_uploaded_film(
    uploaded: Any,
    destination: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write an uploaded film to disk in 8 MiB chunks (up to 5 GB)."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    total = int(getattr(uploaded, "size", 0) or 0)
    read = getattr(uploaded, "read", None)
    seek = getattr(uploaded, "seek", None)
    if callable(seek):
        seek(0)
    if not callable(read):
        raise ValueError("Upload is not a readable film file.")

    def _chunks() -> Any:
        while True:
            chunk = read(8 * 1024 * 1024)
            if not chunk:
                break
            yield chunk

    try:
        return write_film_chunks(
            destination,
            _chunks(),
            max_bytes=MAX_VIDEO_BYTES,
            on_progress=on_progress,
            expected_bytes=total,
        )
    except VideoCollectError as exc:
        raise ValueError(str(exc)) from exc


def film_has_official_tags(path: str | Path) -> bool:
    """Return whether ``path`` itself is an official tag sheet.

    Analyse Stats on a film always runs the automated both-team collect.
    One-team Wyscout XML sitting next to the MP4 must not hijack that path —
    analysts upload their own side's analysis separately under Official tags.
    """

    resolved = normalize_film_path(path)
    try:
        resolved = resolved.resolve()
    except OSError:
        return False
    return resolved.suffix.lower() == ".xml" and resolved.is_file()


def collect_from_film_path(
    path: str | Path,
    *,
    on_progress: ProgressFn | None = None,
    home_kit_hex: str | None = None,
    away_kit_hex: str | None = None,
    home_team_name: str | None = None,
    away_team_name: str | None = None,
    lineup_json: str | None = None,
    force_fresh: bool = False,
) -> MatchRundown:
    """Collect tags from a film (both teams) or from an explicit XML path.

    Film paths always run computer-vision Analyse Stats for Home and Away.
    Sibling one-team Wyscout sheets are ignored here so the automated
    rundown is not replaced by a single-side analysis. Pass the XML path
    itself (or use Official two-team tag sheet) for official tags.
    """

    import json

    from analytics.distinti import MatchFacts, merge_lineups_with_facts
    from analytics.lineups import LineupPlayer, parse_lineup_json

    resolved = normalize_film_path(path)
    try:
        resolved = resolved.resolve()
    except OSError as exc:
        raise ValueError(f"Match film path is not readable ({exc}).") from exc
    if resolved.suffix.lower() == ".xml":
        if not resolved.is_file():
            raise ValueError(f"Tag sheet not found: {resolved}")
        try:
            return collect_from_tag_xml(resolved.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise ValueError(f"Tag XML could not be collected ({exc}).") from exc
    if resolved.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError("Choose a match film (mp4, mov, mkv, avi, m4v, webm) or a tag XML.")
    if not resolved.is_file():
        raise ValueError(f"Match film not found: {resolved}")
    lineups = None
    official_home_goals: int | None = None
    official_away_goals: int | None = None
    if lineup_json and lineup_json.strip():
        try:
            lineups = parse_lineup_json(lineup_json)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Line-up JSON is invalid ({exc}).") from exc
        try:
            payload = json.loads(lineup_json)
        except json.JSONDecodeError:
            payload = {}
        facts_raw = payload.get("match_facts") if isinstance(payload, dict) else None
        if isinstance(facts_raw, dict):
            try:
                hg = facts_raw.get("home_goals")
                ag = facts_raw.get("away_goals")
                official_home_goals = int(hg) if hg is not None else None
                official_away_goals = int(ag) if ag is not None else None
            except (TypeError, ValueError):
                official_home_goals = None
                official_away_goals = None
            fact_home = []
            fact_away = []
            for row in facts_raw.get("home") or []:
                if isinstance(row, dict) and row.get("jersey") is not None and row.get("name"):
                    fact_home.append(
                        LineupPlayer(
                            side="home",
                            jersey=int(row["jersey"]),
                            name=str(row["name"]),
                            position=str(row.get("position") or ""),
                        )
                    )
            for row in facts_raw.get("away") or []:
                if isinstance(row, dict) and row.get("jersey") is not None and row.get("name"):
                    fact_away.append(
                        LineupPlayer(
                            side="away",
                            jersey=int(row["jersey"]),
                            name=str(row["name"]),
                            position=str(row.get("position") or ""),
                        )
                    )
            facts = MatchFacts(
                home_team=str(facts_raw.get("home_team") or home_team_name or "Home"),
                away_team=str(facts_raw.get("away_team") or away_team_name or "Away"),
                home_goals=official_home_goals,
                away_goals=official_away_goals,
                home=tuple(fact_home),
                away=tuple(fact_away),
                source_label=str(facts_raw.get("source") or "distinti"),
            )
            lineups = merge_lineups_with_facts(lineups, facts)
            if facts.home_team and (not home_team_name or home_team_name == "Home"):
                home_team_name = facts.home_team
            if facts.away_team and (not away_team_name or away_team_name == "Away"):
                away_team_name = facts.away_team
    try:
        return collect_from_video(
            resolved,
            on_progress=on_progress,
            home_kit_hex=home_kit_hex,
            away_kit_hex=away_kit_hex,
            home_team_name=home_team_name,
            away_team_name=away_team_name,
            lineups=lineups,
            official_home_goals=official_home_goals,
            official_away_goals=official_away_goals,
            force_fresh=force_fresh,
        )
    except VideoCollectError as exc:
        raise ValueError(str(exc)) from exc


async def persist_rundown(
    base_url: str,
    rundown: MatchRundown,
    *,
    timeout_s: float = INGEST_TIMEOUT_S,
) -> tuple[bool, str]:
    """POST the collected match to FastAPI when the API is reachable.

    Returns:
        ``(persisted, message)``. Local rundown still renders if the API is down.
    """

    origin = base_url.rstrip("/") or DEFAULT_BASE_URL
    online = await probe_api(origin, timeout_s=min(timeout_s, REQUEST_TIMEOUT_S))
    if not online:
        return False, "FastAPI is unreachable — rundown is local only."

    payload = GamePayload(
        match_id=rundown.match_id,
        players=[],
        events=rundown.events,
    )
    body = payload.model_dump(mode="json")
    # Roster is reconstructed server-side from events; send collected names.
    body["players"] = [
        {
            "player_id": str(profile.player_id),
            "team_id": str(profile.team_id),
            "jersey_number": profile.jersey_number,
            "player_name": profile.player_name,
            "position": profile.position,
        }
        for profile in rundown.players
    ]
    url = f"{origin}/api/v1/matches/{rundown.match_id}/ingest"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=body, timeout=timeout_s)
    except httpx.RequestError as exc:
        return False, f"Could not persist rundown ({exc})."
    if response.status_code >= 400:
        return False, f"API ingest failed with HTTP {response.status_code}."
    return True, "Match stored in FastAPI. Subsequent GETs use the collected profiles."


def profile_label(profile: PlayerMatchProfile) -> str:
    """Sidebar label for a collected player."""

    jersey = f"#{profile.jersey_number} " if profile.jersey_number else ""
    name = profile.player_name or profile.position or "Player"
    return f"{jersey}{name}  ·  {profile.player_id}"
