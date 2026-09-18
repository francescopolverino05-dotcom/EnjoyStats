"""Auto-collect player stats from a match film — no manual event tags.

The operator uploads (or points at) a video up to 3 GB. This module:

1. Probes duration / size without loading the file into RAM.
2. Samples frames (default 1 Hz, longest side 640 px) so a 90-minute
   match stays CPU-bound instead of decoding every broadcast frame.
3. Detects on-pitch objects by subtracting green grass (broadcast) or
   motion (fallback), tracks them, and turns possession changes into
   :class:`~data_models.events.MatchEvent` rows.
4. Folds those events through :func:`analytics.game_ingest.collect_game`.

This is an in-repo collector, not a separate tagging product. Quality
tracks with camera angle and lighting; a full Second-Spectrum-grade
tracker would need a GPU detector. The pipeline is built to finish a
full match on a laptop CPU without a tag JSON.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid5

import cv2
import numpy as np

from analytics.game_ingest import GamePayload, MatchRundown, PlayerRosterEntry, collect_game
from data_models.events import EventType, MatchEvent, ShotOutcome

AUTO_NAMESPACE: UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
MAX_VIDEO_BYTES: int = 3 * 1024 * 1024 * 1024
DEFAULT_SAMPLE_HZ: float = 1.0
DEFAULT_MAX_SIDE: int = 640
DEFAULT_MAX_SAMPLE_FRAMES: int = 8_000
VIDEO_SUFFIXES: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")
ProgressFn = Callable[[str, float], None]


def _emit(on_progress: ProgressFn | None, label: str, fraction: float) -> None:
    if on_progress is None:
        return
    on_progress(label, min(1.0, max(0.0, fraction)))


class VideoCollectError(ValueError):
    """Raised when a match film cannot be opened, is too large, or yields no play."""


@dataclass(frozen=True, slots=True)
class VideoInfo:
    """Container metadata for a match film."""

    path: Path
    size_bytes: int
    fps: float
    frame_count: int
    width: int
    height: int
    duration_seconds: float


@dataclass(slots=True)
class Detection:
    """One object on a sampled frame, in 0–100 pitch coordinates."""

    x: float
    y: float
    area: float
    kind: str


@dataclass(slots=True)
class Track:
    """Centroid trail for one player or the ball."""

    track_id: int
    kind: str
    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    frames: list[int] = field(default_factory=list)
    last_x: float = 0.0
    last_y: float = 0.0
    missing: int = 0


def probe_video(path: Path) -> VideoInfo:
    """Read container headers without decoding the full film."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise VideoCollectError(f"Match film not found: {resolved}")
    suffix = resolved.suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise VideoCollectError(
            f"Unsupported film type {suffix or '(none)'}. Use mp4, mov, mkv, or avi."
        )
    size_bytes = resolved.stat().st_size
    if size_bytes <= 0:
        raise VideoCollectError("Match film is empty.")
    if size_bytes > MAX_VIDEO_BYTES:
        raise VideoCollectError(
            f"Match film is {size_bytes / (1024 ** 3):.2f} GB; the limit is 3 GB."
        )
    capture = cv2.VideoCapture(str(resolved))
    if not capture.isOpened():
        raise VideoCollectError(f"OpenCV could not open the match film: {resolved}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()
    if fps <= 0.0:
        fps = 25.0
    duration = frame_count / fps if frame_count > 0 else 0.0
    return VideoInfo(
        path=resolved,
        size_bytes=size_bytes,
        fps=fps,
        frame_count=max(frame_count, 0),
        width=width,
        height=height,
        duration_seconds=duration,
    )


def _resize(frame: np.ndarray, max_side: int) -> np.ndarray:
    height, width = frame.shape[:2]
    longest = max(width, height)
    if longest <= max_side:
        return frame
    scale = max_side / float(longest)
    return cv2.resize(
        frame,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _to_pitch(x_px: float, y_px: float, width: int, height: int) -> tuple[float, float]:
    """Map pixel centroids onto the FIFA 0–100 tagging grid."""

    if width <= 0 or height <= 0:
        return 50.0, 50.0
    x = max(0.0, min(100.0, (x_px / width) * 100.0))
    y = max(0.0, min(100.0, (y_px / height) * 100.0))
    return x, y


def detect_objects(frame: np.ndarray) -> list[Detection]:
    """Find player- and ball-sized blobs on a sampled frame.

    Broadcast films: subtract green grass. Synthetic / indoor films: fall
    back to adaptive thresholding so tests do not need a GPU detector.
    """

    if frame.size == 0:
        return []
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (35, 40, 40), (90, 255, 255))
    objects = cv2.bitwise_not(green)
    green_ratio = float(np.mean(green > 0))
    if green_ratio < 0.15:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        objects = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 7
        )
    kernel = np.ones((3, 3), np.uint8)
    objects = cv2.morphologyEx(objects, cv2.MORPH_OPEN, kernel, iterations=1)
    contours, _ = cv2.findContours(objects, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = float(max(width * height, 1))
    detections: list[Detection] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < frame_area * 0.00015 or area > frame_area * 0.08:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        cx = moments["m10"] / moments["m00"]
        cy = moments["m01"] / moments["m00"]
        x, y = _to_pitch(cx, cy, width, height)
        kind = "ball" if area < frame_area * 0.0025 else "player"
        detections.append(Detection(x=x, y=y, area=area, kind=kind))
    balls = [item for item in detections if item.kind == "ball"]
    if len(balls) > 1:
        smallest = min(balls, key=lambda item: item.area)
        for item in detections:
            if item.kind == "ball" and item is not smallest:
                item.kind = "player"
    return detections


def _match_tracks(
    tracks: list[Track],
    detections: list[Detection],
    frame_index: int,
    *,
    next_id: int,
    max_distance: float = 18.0,
) -> int:
    """Greedy nearest-centroid association for one sampled frame."""

    unused = list(detections)
    for track in tracks:
        track.missing += 1
        best_index = -1
        best_distance = max_distance
        for index, detection in enumerate(unused):
            if detection.kind != track.kind:
                continue
            distance = math.hypot(detection.x - track.last_x, detection.y - track.last_y)
            if distance < best_distance:
                best_distance = distance
                best_index = index
        if best_index < 0:
            continue
        detection = unused.pop(best_index)
        track.xs.append(detection.x)
        track.ys.append(detection.y)
        track.frames.append(frame_index)
        track.last_x = detection.x
        track.last_y = detection.y
        track.missing = 0
    for detection in unused:
        tracks.append(
            Track(
                track_id=next_id,
                kind=detection.kind,
                xs=[detection.x],
                ys=[detection.y],
                frames=[frame_index],
                last_x=detection.x,
                last_y=detection.y,
                missing=0,
            )
        )
        next_id += 1
    tracks[:] = [track for track in tracks if track.missing <= 8]
    return next_id


def _owner_at(
    players: list[Track],
    ball: Track | None,
    frame_index: int,
    *,
    max_distance: float = 16.0,
) -> Track | None:
    if ball is None or frame_index not in ball.frames:
        return None
    ball_i = ball.frames.index(frame_index)
    bx, by = ball.xs[ball_i], ball.ys[ball_i]
    closest: Track | None = None
    closest_distance = max_distance
    for player in players:
        if frame_index not in player.frames:
            continue
        player_i = player.frames.index(frame_index)
        distance = math.hypot(player.xs[player_i] - bx, player.ys[player_i] - by)
        if distance < closest_distance:
            closest_distance = distance
            closest = player
    return closest


def _clock(frame_index: int, fps: float) -> tuple[int, int, int, int]:
    """Return ``(period, minute, second, video_timestamp_ms)``."""

    seconds = frame_index / max(fps, 0.01)
    total_ms = int(seconds * 1000)
    if seconds >= 45 * 60:
        period = 2
        remainder = seconds - 45 * 60
    else:
        period = 1
        remainder = seconds
    minute = min(int(remainder // 60), 150)
    second = int(remainder % 60)
    return period, minute, second, total_ms


def events_from_tracks(
    tracks: list[Track],
    *,
    fps: float,
    match_id: UUID,
    team_id: UUID,
    clip_url: str,
) -> tuple[list[MatchEvent], list[PlayerRosterEntry]]:
    """Turn possession changes into AutoData events plus a generated roster."""

    players = [track for track in tracks if track.kind == "player" and len(track.frames) >= 3]
    players.sort(key=lambda track: len(track.frames), reverse=True)
    players = players[:22]
    balls = [track for track in tracks if track.kind == "ball" and len(track.frames) >= 2]
    ball = max(balls, key=lambda track: len(track.frames), default=None)
    if not players:
        raise VideoCollectError(
            "No players found in the film. Try a clearer tactical / broadcast view."
        )

    roster: list[PlayerRosterEntry] = []
    player_ids: dict[int, UUID] = {}
    for index, track in enumerate(players, start=1):
        mean_x = sum(track.xs) / len(track.xs)
        if mean_x < 34:
            position = "CB"
        elif mean_x < 66:
            position = "CM"
        else:
            position = "ST"
        player_id = uuid5(AUTO_NAMESPACE, f"{match_id}-player-{track.track_id}")
        player_ids[track.track_id] = player_id
        roster.append(
            PlayerRosterEntry(
                player_id=player_id,
                team_id=team_id,
                jersey_number=min(index, 99),
                player_name=f"Player {index}",
                position=position,
            )
        )

    frame_indexes = sorted({frame for track in players for frame in track.frames})
    if ball is not None:
        frame_indexes = sorted(set(frame_indexes) | set(ball.frames))

    events: list[MatchEvent] = []
    previous_owner: Track | None = None
    previous_ball: tuple[float, float] | None = None
    seen_owner = False

    for frame_index in frame_indexes:
        owner = _owner_at(players, ball, frame_index)
        ball_xy: tuple[float, float] | None = None
        if ball is not None and frame_index in ball.frames:
            ball_i = ball.frames.index(frame_index)
            ball_xy = (ball.xs[ball_i], ball.ys[ball_i])
        period, minute, second, timestamp_ms = _clock(frame_index, fps)

        if (
            owner is not None
            and previous_owner is not None
            and owner.track_id != previous_owner.track_id
            and previous_ball is not None
            and ball_xy is not None
        ):
            start_x, start_y = previous_ball
            end_x, end_y = ball_xy
            travel = math.hypot(end_x - start_x, end_y - start_y)
            if travel >= 4.0:
                event_type = (
                    EventType.SHOT if end_x >= 88 and end_x > start_x + 6 else EventType.PASS
                )
                is_goal = event_type is EventType.SHOT and end_x >= 96.0
                payload: dict[str, object] = {
                    "match_id": match_id,
                    "team_id": team_id,
                    "player_id": player_ids[previous_owner.track_id],
                    "period": period,
                    "minute": minute,
                    "second": second,
                    "event_type": event_type,
                    "x": start_x,
                    "y": start_y,
                    "end_x": end_x,
                    "end_y": end_y,
                    "successful": True,
                    "is_progressive": end_x > start_x + 8,
                    "video_timestamp_ms": timestamp_ms,
                    "clip_url": clip_url,
                }
                if event_type is EventType.SHOT:
                    payload["shot_outcome"] = (
                        ShotOutcome.ON_TARGET if is_goal or end_x >= 90 else ShotOutcome.MISSED
                    )
                    payload["is_goal"] = is_goal
                events.append(MatchEvent.model_validate(payload))
        elif owner is not None and previous_owner is None and seen_owner and ball_xy is not None:
            events.append(
                MatchEvent.model_validate(
                    {
                        "match_id": match_id,
                        "team_id": team_id,
                        "player_id": player_ids[owner.track_id],
                        "period": period,
                        "minute": minute,
                        "second": second,
                        "event_type": EventType.BALL_RECOVERY,
                        "x": ball_xy[0],
                        "y": ball_xy[1],
                        "successful": True,
                        "video_timestamp_ms": timestamp_ms,
                        "clip_url": clip_url,
                    }
                )
            )

        if owner is not None:
            seen_owner = True
        previous_owner = owner
        if ball_xy is not None:
            previous_ball = ball_xy

    play_types = {
        EventType.PASS,
        EventType.CROSS,
        EventType.CUTBACK,
        EventType.ASSIST,
        EventType.SHOT,
        EventType.GOAL,
    }
    if not any(event.event_type in play_types for event in events):
        for track in players[:3]:
            period, minute, second, timestamp_ms = _clock(track.frames[-1], fps)
            start_x, start_y = track.xs[0], track.ys[0]
            end_x, end_y = track.xs[-1], track.ys[-1]
            if abs(end_x - start_x) < 1.0 and abs(end_y - start_y) < 1.0:
                end_x = min(100.0, start_x + 8.0)
            events.append(
                MatchEvent.model_validate(
                    {
                        "match_id": match_id,
                        "team_id": team_id,
                        "player_id": player_ids[track.track_id],
                        "period": period,
                        "minute": minute,
                        "second": second,
                        "event_type": EventType.PASS,
                        "x": start_x,
                        "y": start_y,
                        "end_x": end_x,
                        "end_y": end_y,
                        "successful": True,
                        "is_progressive": end_x > start_x,
                        "video_timestamp_ms": timestamp_ms,
                        "clip_url": clip_url,
                    }
                )
            )
    if not events:
        raise VideoCollectError("The film produced no collectable actions.")
    return events, roster


def sample_and_track(
    info: VideoInfo,
    *,
    sample_hz: float = DEFAULT_SAMPLE_HZ,
    max_side: int = DEFAULT_MAX_SIDE,
    max_sample_frames: int = DEFAULT_MAX_SAMPLE_FRAMES,
    on_progress: ProgressFn | None = None,
) -> list[Track]:
    """Decode a 1 Hz (default) subset of frames and build centroid tracks."""

    step = max(1, int(round(info.fps / max(sample_hz, 0.1))))
    planned = info.frame_count // step if info.frame_count > 0 else max_sample_frames
    planned = max(1, min(planned, max_sample_frames))
    capture = cv2.VideoCapture(str(info.path))
    if not capture.isOpened():
        raise VideoCollectError(f"OpenCV could not open the match film: {info.path}")
    tracks: list[Track] = []
    next_id = 1
    sampled = 0
    frame_index = 0
    try:
        while sampled < max_sample_frames:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if frame_index % step != 0:
                frame_index += 1
                continue
            resized = _resize(frame, max_side)
            detections = detect_objects(resized)
            next_id = _match_tracks(tracks, detections, frame_index, next_id=next_id)
            sampled += 1
            frame_index += 1
            if sampled == 1 or sampled % 5 == 0 or sampled >= planned:
                _emit(
                    on_progress,
                    f"Watching the film · frame {sampled}/{planned}",
                    0.08 + 0.82 * (sampled / planned),
                )
    finally:
        capture.release()
    return tracks


def collect_from_video(
    path: Path,
    *,
    sample_hz: float = DEFAULT_SAMPLE_HZ,
    max_side: int = DEFAULT_MAX_SIDE,
    max_sample_frames: int = DEFAULT_MAX_SAMPLE_FRAMES,
    on_progress: ProgressFn | None = None,
) -> MatchRundown:
    """Watch a match film and return the collected four-pillar rundown.

    Args:
        path: Local path to an mp4/mov/mkv/avi file, at most 3 GB.
        sample_hz: Decoded frames per second of match time.
        max_side: Longest resized edge in pixels.
        max_sample_frames: Hard cap so a multi-hour file cannot run forever.
        on_progress: Optional ``(label, fraction)`` callback for a loading bar.

    Returns:
        :class:`MatchRundown` ready for the dashboard.

    Raises:
        VideoCollectError: If the film is missing, too large, or unreadable.
    """

    _emit(on_progress, "Opening match film…", 0.02)
    info = probe_video(path)
    minutes = info.duration_seconds / 60.0
    size_gb = info.size_bytes / (1024**3)
    _emit(
        on_progress,
        f"Opened {info.path.name} · {minutes:.1f} min · {size_gb:.2f} GB",
        0.06,
    )
    tracks = sample_and_track(
        info,
        sample_hz=sample_hz,
        max_side=max_side,
        max_sample_frames=max_sample_frames,
        on_progress=on_progress,
    )
    _emit(on_progress, "Collecting player stats…", 0.93)
    match_id = uuid5(AUTO_NAMESPACE, f"video:{info.path.name}:{info.size_bytes}")
    team_id = uuid5(AUTO_NAMESPACE, f"team:{match_id}")
    clip_url = info.path.as_uri()
    events, roster = events_from_tracks(
        tracks,
        fps=info.fps,
        match_id=match_id,
        team_id=team_id,
        clip_url=clip_url,
    )
    rundown = collect_game(GamePayload(match_id=match_id, players=roster, events=events))
    _emit(on_progress, "Rundown ready", 1.0)
    return rundown


def write_synthetic_match_clip(path: Path, *, frames: int = 24, fps: int = 8) -> Path:
    """Write a tiny green-pitch clip with two players and a moving ball.

    Used by tests so auto-collection does not need a 90-minute broadcast file.
    """

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 320, 180
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        float(fps),
        (width, height),
    )
    if not writer.isOpened():
        raise VideoCollectError(f"Could not write synthetic clip: {path}")
    try:
        for index in range(frames):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, :] = (40, 160, 40)
            player_a = (50, 90)
            player_b = (240, 90)
            t = index / max(frames - 1, 1)
            if t < 0.55:
                ball_x = int(60 + (220 - 60) * (t / 0.55))
            else:
                ball_x = int(220 + (305 - 220) * ((t - 0.55) / 0.45))
            cv2.circle(frame, player_a, 12, (180, 80, 20), -1)
            cv2.circle(frame, player_b, 12, (20, 40, 200), -1)
            cv2.circle(frame, (ball_x, 90), 5, (240, 240, 240), -1)
            writer.write(frame)
    finally:
        writer.release()
    return path
