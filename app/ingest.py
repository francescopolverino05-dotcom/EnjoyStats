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
from analytics.video_auto_collect import (
    MAX_VIDEO_BYTES,
    VIDEO_SUFFIXES,
    ProgressFn,
    collect_from_video,
)
from app.client import DEFAULT_BASE_URL, REQUEST_TIMEOUT_S, ProfileLoad, probe_api
from app.dummy_data import PitchAction
from app.metrics import directions_from_distribution
from data_models.events import EventType, MatchEvent
from data_models.player_stats import PlayerMatchProfile

INGEST_TIMEOUT_S = 30.0


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


def collect_sample_match() -> MatchRundown:
    """Collect the bundled sample game without an uploaded file."""

    return collect_game(sample_game_payload())


def collect_uploaded_bytes(raw_bytes: bytes) -> MatchRundown:
    """Parse an uploaded JSON game file and collect player stats."""

    try:
        decoded: Any = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Game file is not valid JSON ({exc}).") from exc
    return collect_game(parse_game_payload(decoded))


def save_uploaded_film(
    uploaded: Any,
    destination: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write an uploaded film to disk in 8 MiB chunks (up to 3 GB)."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    total = int(getattr(uploaded, "size", 0) or 0)
    read = getattr(uploaded, "read", None)
    seek = getattr(uploaded, "seek", None)
    if callable(seek):
        seek(0)
    if not callable(read):
        raise ValueError("Upload is not a readable film file.")
    with destination.open("wb") as out:
        while True:
            chunk = read(8 * 1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_VIDEO_BYTES:
                out.close()
                destination.unlink(missing_ok=True)
                raise ValueError("Match film exceeds the 3 GB upload limit.")
            out.write(chunk)
            if on_progress is not None:
                on_progress(written, total if total > 0 else written)
    if written <= 0:
        destination.unlink(missing_ok=True)
        raise ValueError("Uploaded film is empty.")
    if on_progress is not None:
        on_progress(written, total if total > 0 else written)
    return destination


def collect_from_film_path(
    path: str | Path,
    *,
    on_progress: ProgressFn | None = None,
) -> MatchRundown:
    """Auto-tag a match film on disk and collect the four-pillar rundown."""

    resolved = Path(path).expanduser()
    if resolved.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError("Choose a match film (mp4, mov, mkv, avi, m4v, webm), not a tag JSON.")
    return collect_from_video(resolved, on_progress=on_progress)


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
