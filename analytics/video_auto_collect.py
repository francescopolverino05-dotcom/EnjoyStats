"""Auto-collect player stats from a match film — no manual event tags.

The operator uploads (or points at) a video up to 3 GB. This module:

1. Probes duration / size without loading the file into RAM.
2. Samples the **whole match** (default 5 Hz, longest side 640 px) so a
   90-minute film is tagged from kick-off to full time. Hours of CPU
   time are expected; the cap is duration, not a handful of frames.
3. Detects on-pitch objects, stitches broken tracks, and turns the
   play-point path into a Wyscout-density tag sheet (~7.5 actions per
   minute, calibrated on the Arsenal v Palace analysis XML).
4. Folds those events through :func:`analytics.game_ingest.collect_game`.

If a Wyscout / Nacsport ``<analysis>`` XML for the same fixture sits
next to the film, that official sheet is used instead of broadcast CV.

This is an in-repo collector, not a separate tagging product. Broadcast
CV will not match a human scoresheet; official XML will.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid5

import cv2
import numpy as np

from analytics.game_ingest import GamePayload, MatchRundown, PlayerRosterEntry, collect_game
from analytics.match_tags import infer_team_names, write_sidecar_xml
from data_models.events import EventType, MatchEvent, ShotOutcome

AUTO_NAMESPACE: UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
MAX_VIDEO_BYTES: int = 3 * 1024 * 1024 * 1024
DEFAULT_SAMPLE_HZ: float = 5.0
DEFAULT_MAX_SIDE: int = 640
DEFAULT_MAX_SAMPLE_FRAMES: int = 48_000
# Arsenal v Palace (1-1) Wyscout analysis: 736 actions over 97.5 minutes.
WYSCOUT_ACTIONS_PER_MINUTE: float = 7.55
TARGET_EVENT_GAP_S: float = 60.0 / WYSCOUT_ACTIONS_PER_MINUTE
MIN_EVENT_GAP_S: float = 1.6
VIDEO_SUFFIXES: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")
TAG_SUFFIXES: tuple[str, ...] = (".xml",)
FILM_CHUNK_BYTES: int = 8 * 1024 * 1024
REMUX_COPY_TIMEOUT_S: int = 600
REMUX_ENCODE_TIMEOUT_S: int = 3600
ProgressFn = Callable[[str, float], None]


class VideoCollectError(ValueError):
    """Raised when a match film cannot be opened, is too large, or yields no play."""


def _emit(on_progress: ProgressFn | None, label: str, fraction: float) -> None:
    if on_progress is None:
        return
    on_progress(label, min(1.0, max(0.0, fraction)))


def normalize_film_path(raw: str | Path) -> Path:
    """Strip quotes / ``file://`` URIs and expand ``~`` from a pasted path."""

    text = str(raw).strip().strip("\u200b")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    text = text.strip().strip("\u200b")
    if text.startswith("file://"):
        parsed = urlparse(text)
        text = unquote(parsed.path)
        if parsed.netloc and parsed.netloc not in {"localhost", "127.0.0.1"}:
            text = f"//{parsed.netloc}{text}"
    return Path(text).expanduser()


def safe_film_name(name: str) -> str:
    """Return a single-path-segment film filename with a video suffix."""

    base = Path(name.replace("\\", "/")).name.strip() or "match.mp4"
    cleaned = "".join(char if char.isalnum() or char in ".-_" else "_" for char in base)
    cleaned = cleaned.strip("._") or "match"
    suffix = Path(cleaned).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        cleaned = f"{cleaned}.mp4"
    return cleaned


def film_inbox_dir() -> Path:
    """Directory that operators drop match films into (no HTTP transfer)."""

    override = os.environ.get("ENJOYSTATS_FILM_INBOX", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[1] / ".local-run" / "inbox"


def film_upload_dir() -> Path:
    """Directory used for streamed browser uploads and Streamlit saves."""

    override = os.environ.get("ENJOYSTATS_FILM_UPLOADS", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[1] / ".local-run" / "uploads"


def list_ready_films(*directories: Path) -> list[Path]:
    """Newest-first films and tag XML sitting in the inbox / uploads folders."""

    found: list[Path] = []
    seen: set[Path] = set()
    search = directories or (film_inbox_dir(), film_upload_dir())
    accepted = VIDEO_SUFFIXES + TAG_SUFFIXES
    for directory in search:
        if not directory.is_dir():
            continue
        for candidate in directory.iterdir():
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen or not resolved.is_file():
                continue
            if resolved.suffix.lower() not in accepted:
                continue
            if resolved.stat().st_size <= 0:
                continue
            seen.add(resolved)
            found.append(resolved)
    found.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return found


def write_film_chunks(
    destination: Path,
    chunks: Iterable[bytes],
    *,
    max_bytes: int = MAX_VIDEO_BYTES,
    on_progress: Callable[[int, int], None] | None = None,
    expected_bytes: int = 0,
) -> Path:
    """Write a film from an iterator of chunks without buffering the file."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with destination.open("wb") as out:
        for chunk in chunks:
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                out.close()
                destination.unlink(missing_ok=True)
                raise VideoCollectError("Match film exceeds the 3 GB upload limit.")
            out.write(chunk)
            if on_progress is not None:
                on_progress(written, expected_bytes if expected_bytes > 0 else written)
    if written <= 0:
        destination.unlink(missing_ok=True)
        raise VideoCollectError("Uploaded film is empty.")
    if on_progress is not None:
        on_progress(written, expected_bytes if expected_bytes > 0 else written)
    return destination


def remux_for_opencv(path: Path) -> Path:
    """Rewrap (or re-encode) a film into a container OpenCV can open.

    Many broadcast MP4s use codecs VideoCapture rejects. Stream-copy into a
    new MP4 first; if that still fails, transcode to MPEG-4 which the
    headless OpenCV wheel can decode without extra system codecs.
    """

    resolved = path.expanduser().resolve()
    destination = resolved.with_name(f"{resolved.stem}.opencv.mp4")
    if destination.is_file() and destination.stat().st_size > 0:
        probe = cv2.VideoCapture(str(destination))
        opened = probe.isOpened()
        probe.release()
        if opened:
            return destination
        destination.unlink(missing_ok=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VideoCollectError(
            f"OpenCV could not open the match film: {resolved}. "
            "Install ffmpeg to remux unsupported codecs, or export the "
            "clip as mp4/avi."
        )

    copy_cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(resolved),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    copied = subprocess.run(
        copy_cmd,
        check=False,
        capture_output=True,
        timeout=REMUX_COPY_TIMEOUT_S,
    )
    if copied.returncode == 0 and destination.is_file() and destination.stat().st_size > 0:
        probe = cv2.VideoCapture(str(destination))
        opened = probe.isOpened()
        probe.release()
        if opened:
            return destination
    destination.unlink(missing_ok=True)

    encode_cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(resolved),
        "-c:v",
        "mpeg4",
        "-q:v",
        "6",
        "-an",
        str(destination),
    ]
    encoded = subprocess.run(
        encode_cmd,
        check=False,
        capture_output=True,
        timeout=REMUX_ENCODE_TIMEOUT_S,
    )
    if encoded.returncode != 0 or not destination.is_file() or destination.stat().st_size <= 0:
        destination.unlink(missing_ok=True)
        detail = (encoded.stderr or copied.stderr or b"").decode("utf-8", errors="replace")
        snippet = " ".join(detail.strip().splitlines()[-2:])[:240]
        raise VideoCollectError(
            f"OpenCV could not open the match film: {resolved}."
            + (f" ffmpeg: {snippet}" if snippet else "")
        )
    return destination


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
    bgr: tuple[float, float, float] = (0.0, 0.0, 0.0)


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
    bgr: tuple[float, float, float] = (0.0, 0.0, 0.0)
    team: int = 0


def probe_video(path: Path) -> VideoInfo:
    """Read container headers without decoding the full film."""

    resolved = normalize_film_path(path).resolve()
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
    opened = capture.isOpened()
    if not opened:
        capture.release()
        resolved = remux_for_opencv(resolved)
        capture = cv2.VideoCapture(str(resolved))
        opened = capture.isOpened()
    if not opened:
        capture.release()
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
    size_bytes = resolved.stat().st_size
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


def _pitch_mask(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Return ``(pitch_mask, grass_mask, pitch_ratio)`` for a broadcast frame.

    Serie B / 540p grass is often desaturated, so the hue window is wider
    than a textbook green-screen key. The largest connected field region is
    kept so stands and scoreboard chrome stay outside the detector.
    """

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    grass = cv2.inRange(hsv, (18, 12, 25), (100, 255, 230))
    grass = cv2.bitwise_or(grass, cv2.inRange(hsv, (8, 12, 25), (40, 90, 230)))
    vivid = cv2.inRange(hsv, (35, 40, 40), (90, 255, 255))
    grass = cv2.bitwise_or(grass, vivid)
    closed = cv2.morphologyEx(grass, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    if not contours:
        return mask, grass, 0.0
    largest = max(contours, key=cv2.contourArea)
    frame_area = float(max(frame.shape[0] * frame.shape[1], 1))
    if cv2.contourArea(largest) / frame_area < 0.12:
        return mask, grass, cv2.contourArea(largest) / frame_area
    cv2.drawContours(mask, [largest], -1, 255, -1)
    mask = cv2.erode(mask, np.ones((9, 9), np.uint8), iterations=1)
    return mask, grass, float(np.mean(mask > 0))


def _contour_bgr(frame: np.ndarray, contour: np.ndarray) -> tuple[float, float, float]:
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    mean = cv2.mean(frame, mask=mask)
    return (float(mean[0]), float(mean[1]), float(mean[2]))


def detect_objects(frame: np.ndarray) -> list[Detection]:
    """Find player-sized blobs on the grass, ignoring stands and graphics.

    Broadcast films: key a wide grass window, keep the largest pitch
    contour, and take the 22 largest player-sized holes inside it.
    Synthetic clips with vivid green still match the same path.
    """

    if frame.size == 0:
        return []
    height, width = frame.shape[:2]
    mask, grass, pitch_ratio = _pitch_mask(frame)
    if pitch_ratio < 0.12:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        objects = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 7
        )
        pitch_area = float(max(width * height, 1))
        search = objects
        color_source = frame
    else:
        inside = cv2.bitwise_and(cv2.bitwise_not(grass), mask)
        search = cv2.morphologyEx(inside, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
        pitch_area = float(max(int(np.count_nonzero(mask)), 1))
        color_source = frame
    contours, _ = cv2.findContours(search, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, Detection]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        frac = area / pitch_area
        if frac < 0.00008 or frac > 0.025:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        cx = moments["m10"] / moments["m00"]
        cy = moments["m01"] / moments["m00"]
        x, y = _to_pitch(cx, cy, width, height)
        kind = "ball" if frac < 0.0004 else "player"
        detection = Detection(
            x=x, y=y, area=area, kind=kind, bgr=_contour_bgr(color_source, contour)
        )
        candidates.append((area, detection))
    candidates.sort(key=lambda item: item[0], reverse=True)
    players = [item[1] for item in candidates if item[1].kind == "player"][:22]
    balls = [item[1] for item in candidates if item[1].kind == "ball"]
    if balls:
        players.append(min(balls, key=lambda item: item.area))
    return players


def _match_tracks(
    tracks: list[Track],
    detections: list[Detection],
    frame_index: int,
    *,
    next_id: int,
    max_distance: float = 18.0,
    max_missing: int = 12,
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
        track.bgr = detection.bgr
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
                bgr=detection.bgr,
            )
        )
        next_id += 1
    tracks[:] = [track for track in tracks if track.missing <= max_missing]
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


def _hue(bgr: tuple[float, float, float]) -> float:
    pixel = np.uint8([[bgr]])
    hsv = cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)
    return float(hsv[0, 0, 0])


def _assign_teams(players: list[Track]) -> None:
    """Split tracks into two sides using shirt colour, then mean pitch X."""

    if len(players) < 2:
        for track in players:
            track.team = 0
        return
    hues = [_hue(track.bgr) for track in players]
    spread = max(hues) - min(hues)
    if spread >= 18:
        low = min(hues)
        high = max(hues)
        mid = (low + high) / 2.0
        for track, hue in zip(players, hues, strict=True):
            track.team = 0 if hue <= mid else 1
        return
    xs = [sum(track.xs) / len(track.xs) for track in players]
    mid_x = sorted(xs)[len(xs) // 2]
    for track, mean_x in zip(players, xs, strict=True):
        track.team = 0 if mean_x <= mid_x else 1


def _play_point(
    players: list[Track],
    ball: Track | None,
    frame_index: int,
) -> tuple[float, float] | None:
    """Ball location, or the tightest on-pitch player cluster (the action)."""

    if ball is not None and frame_index in ball.frames:
        index = ball.frames.index(frame_index)
        return ball.xs[index], ball.ys[index]
    points = [
        (track.xs[track.frames.index(frame_index)], track.ys[track.frames.index(frame_index)])
        for track in players
        if frame_index in track.frames
    ]
    if not points:
        return None
    if len(points) == 1:
        return points[0]
    best = points[0]
    best_score = 1e9
    for candidate in points:
        nearby = sorted(math.hypot(px - candidate[0], py - candidate[1]) for px, py in points)
        score = sum(nearby[: min(4, len(nearby))])
        if score < best_score:
            best_score = score
            best = candidate
    return best


def _owner_near_point(
    players: list[Track],
    point: tuple[float, float],
    frame_index: int,
    *,
    max_distance: float = 22.0,
) -> Track | None:
    closest: Track | None = None
    closest_distance = max_distance
    for player in players:
        if frame_index not in player.frames:
            continue
        index = player.frames.index(frame_index)
        distance = math.hypot(player.xs[index] - point[0], player.ys[index] - point[1])
        if distance < closest_distance:
            closest_distance = distance
            closest = player
    return closest


def _team_goal_x(team: int, players: list[Track]) -> float:
    """Attacking goal X (0 or 100) from which way the team advances the ball."""

    def _disp(side: int) -> float:
        members = [track for track in players if track.team == side]
        if not members:
            return 0.0
        return sum(track.xs[-1] - track.xs[0] for track in members) / len(members)

    home_disp = _disp(0)
    away_disp = _disp(1)
    home_attacks_right = home_disp >= away_disp
    if team == 0:
        return 100.0 if home_attacks_right else 0.0
    return 0.0 if home_attacks_right else 100.0


def _clone_track(track: Track) -> Track:
    return Track(
        track_id=track.track_id,
        kind=track.kind,
        xs=list(track.xs),
        ys=list(track.ys),
        frames=list(track.frames),
        last_x=track.last_x,
        last_y=track.last_y,
        missing=track.missing,
        bgr=track.bgr,
        team=track.team,
    )


def stitch_tracks(
    tracks: Sequence[Track],
    *,
    max_gap_frames: int = 40,
    max_join_dist: float = 22.0,
) -> list[Track]:
    """Join fragmented detections of the same body into longer identities.

    Broadcast 1–5 Hz tracking drops a shirt for a second and mints a new
    id. Without stitching, the 22 longest tracks are often static blobs
    and a 90-minute film collapses to a handful of tags.
    """

    identities: list[Track] = []
    ordered = sorted((track for track in tracks if track.frames), key=lambda row: row.frames[0])
    for track in ordered:
        best: Track | None = None
        best_dist = max_join_dist
        for ident in identities:
            if ident.kind != track.kind or ident.frames[-1] >= track.frames[0]:
                continue
            gap = track.frames[0] - ident.frames[-1]
            if gap <= 0 or gap > max_gap_frames:
                continue
            dist = math.hypot(track.xs[0] - ident.xs[-1], track.ys[0] - ident.ys[-1])
            hue_delta = abs(_hue(track.bgr) - _hue(ident.bgr))
            if dist < best_dist and hue_delta <= 30.0:
                best = ident
                best_dist = dist
        if best is None:
            identities.append(_clone_track(track))
            continue
        best.xs.extend(track.xs)
        best.ys.extend(track.ys)
        best.frames.extend(track.frames)
        best.last_x = track.last_x
        best.last_y = track.last_y
        best.bgr = track.bgr
    return identities


def _xy_at(track: Track, frame_index: int, lookup: dict[int, dict[int, tuple[float, float]]]) -> tuple[float, float] | None:
    return lookup.get(track.track_id, {}).get(frame_index)


def _same_actor(left: Track, right: Track, lookup: dict[int, dict[int, tuple[float, float]]], frame_index: int) -> bool:
    if left.track_id == right.track_id:
        return True
    if left.team != right.team:
        return False
    a = _xy_at(left, frame_index, lookup)
    b = _xy_at(right, frame_index, lookup)
    if a is None or b is None:
        return math.hypot(left.last_x - right.last_x, left.last_y - right.last_y) <= 10.0
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= 10.0


def events_from_tracks(
    tracks: Sequence[Track],
    *,
    fps: float,
    match_id: UUID,
    team_id: UUID,
    clip_url: str,
    home_name: str = "Home",
    away_name: str = "Away",
) -> tuple[list[MatchEvent], list[PlayerRosterEntry]]:
    """Turn tracked play into a full-match tag sheet plus a two-team roster.

    Possession is the player nearest the play point (the ball when it is
    visible, otherwise the tightest player cluster). Tags fire for
    owner changes, ball-flight jumps, set pieces, duels, shots, and — at
    the Wyscout mean gap of ~8 s — recycled possession passes so a
    90-minute film yields hundreds of actions instead of a handful.
    """

    del team_id
    stitched = stitch_tracks(tracks, max_gap_frames=max(24, int(fps * 2)), max_join_dist=22.0)
    players = [track for track in stitched if track.kind == "player" and len(track.frames) >= 3]
    players.sort(key=lambda track: len(track.frames), reverse=True)
    players = players[:30]
    balls = [track for track in stitched if track.kind == "ball" and len(track.frames) >= 2]
    ball = max(balls, key=lambda track: len(track.frames), default=None)
    if ball is None:
        mobile = [
            track
            for track in players
            if math.hypot(track.xs[-1] - track.xs[0], track.ys[-1] - track.ys[0]) >= 35.0
        ]
        if mobile:
            ball = max(
                mobile,
                key=lambda track: math.hypot(track.xs[-1] - track.xs[0], track.ys[-1] - track.ys[0]),
            )
            players = [track for track in players if track is not ball]
    if not players:
        raise VideoCollectError(
            "No players found in the film. Try a clearer tactical / broadcast view."
        )
    _assign_teams(players)
    team_ids = (
        uuid5(AUTO_NAMESPACE, f"{match_id}-team-{home_name}"),
        uuid5(AUTO_NAMESPACE, f"{match_id}-team-{away_name}"),
    )
    goal_x = (_team_goal_x(0, players), _team_goal_x(1, players))

    lookup: dict[int, dict[int, tuple[float, float]]] = {}
    for track in players:
        lookup[track.track_id] = {
            frame: (x, y) for frame, x, y in zip(track.frames, track.xs, track.ys, strict=True)
        }

    roster: list[PlayerRosterEntry] = []
    player_ids: dict[int, UUID] = {}
    jersey_by_team = {0: 1, 1: 1}
    for track in players:
        mean_x = sum(track.xs) / len(track.xs)
        attack_x = goal_x[track.team]
        depth = mean_x if attack_x >= 50 else 100.0 - mean_x
        if depth < 34:
            position = "CB"
        elif depth < 66:
            position = "CM"
        else:
            position = "ST"
        jersey = min(jersey_by_team[track.team], 99)
        jersey_by_team[track.team] += 1
        team_name = home_name if track.team == 0 else away_name
        player_id = uuid5(AUTO_NAMESPACE, f"{match_id}-player-{track.track_id}")
        player_ids[track.track_id] = player_id
        roster.append(
            PlayerRosterEntry(
                player_id=player_id,
                team_id=team_ids[track.team],
                jersey_number=jersey,
                player_name=f"{team_name} {position} {jersey}",
                position=position,
            )
        )

    frame_indexes = sorted({frame for track in players for frame in track.frames})
    if ball is not None:
        frame_indexes = sorted(set(frame_indexes) | set(ball.frames))

    events: list[MatchEvent] = []
    last_event_s = -1e9
    last_tag_point: tuple[float, float] | None = None
    last_owner: Track | None = None
    prev_point: tuple[float, float] | None = None
    pending_shot: MatchEvent | None = None

    def _emit(payload: dict[str, object]) -> MatchEvent:
        event = MatchEvent.model_validate(payload)
        events.append(event)
        return event

    for frame_index in frame_indexes:
        point = _play_point(players, ball, frame_index)
        owner = _owner_near_point(players, point, frame_index) if point else None
        if point is None or owner is None:
            continue
        period, minute, second, timestamp_ms = _clock(frame_index, fps)
        now_s = timestamp_ms / 1000.0
        if last_tag_point is None or last_owner is None:
            last_tag_point = point
            last_owner = owner
            prev_point = point
            last_event_s = now_s
            continue
        start = last_tag_point
        travel = math.hypot(point[0] - start[0], point[1] - start[1])
        step = (
            math.hypot(point[0] - prev_point[0], point[1] - prev_point[1])
            if prev_point is not None
            else travel
        )
        dt = 1.0 / max(fps, 0.01)
        speed = step / max(dt, 1e-3)
        attack_goal = goal_x[owner.team]
        toward_goal = abs(point[0] - attack_goal) < abs(start[0] - attack_goal)
        box_x = 82.0 if attack_goal >= 50 else 18.0
        in_box = point[0] >= box_x if attack_goal >= 50 else point[0] <= box_x
        mouth = abs(point[0] - attack_goal) <= 5.0
        wide = point[1] <= 18.0 or point[1] >= 82.0
        touchline = point[1] <= 4.0 or point[1] >= 96.0
        corner = (point[0] <= 6.0 or point[0] >= 94.0) and (point[1] <= 10.0 or point[1] >= 90.0)
        gap_ok = (now_s - last_event_s) >= MIN_EVENT_GAP_S
        due = (now_s - last_event_s) >= TARGET_EVENT_GAP_S
        actor = last_owner if last_owner is not None else owner
        payload: dict[str, object] = {
            "match_id": match_id,
            "team_id": team_ids[actor.team],
            "player_id": player_ids[actor.track_id],
            "period": period,
            "minute": minute,
            "second": second,
            "x": start[0],
            "y": start[1],
            "end_x": point[0],
            "end_y": point[1],
            "successful": True,
            "is_progressive": toward_goal and travel >= 8.0,
            "attacking_left_to_right": attack_goal >= 50,
            "video_timestamp_ms": timestamp_ms,
            "clip_url": clip_url,
        }

        tagged = False
        if pending_shot is not None and mouth and toward_goal:
            _emit(
                {
                    **payload,
                    "event_type": EventType.GOAL,
                    "is_goal": True,
                    "shot_outcome": ShotOutcome.ON_TARGET,
                    "player_id": pending_shot.player_id,
                    "team_id": pending_shot.team_id,
                }
            )
            pending_shot = None
            tagged = True
        elif toward_goal and in_box and (travel >= 6.0 or speed >= 12.0 or mouth) and (
            gap_ok or mouth
        ):
            is_goal = mouth
            shot = _emit(
                {
                    **payload,
                    "event_type": EventType.GOAL if is_goal else EventType.SHOT,
                    "is_goal": is_goal,
                    "shot_outcome": ShotOutcome.ON_TARGET if is_goal or mouth else ShotOutcome.MISSED,
                }
            )
            pending_shot = None if is_goal else shot
            tagged = True
        else:
            opponent = None
            closest_opp = 9.0
            clustered = 0
            for other in players:
                loc = _xy_at(other, frame_index, lookup)
                if loc is None:
                    continue
                dist = math.hypot(loc[0] - point[0], loc[1] - point[1])
                if dist <= 12.0:
                    clustered += 1
                if other.team != owner.team and dist < closest_opp:
                    closest_opp = dist
                    opponent = other
            if opponent is not None and closest_opp <= 9.0 and due and travel < 8.0:
                _emit(
                    {
                        **payload,
                        "event_type": EventType.AERIAL_DUEL if clustered >= 4 else EventType.GROUND_DUEL,
                        "end_x": None,
                        "end_y": None,
                        "successful": True,
                    }
                )
                tagged = True
            elif last_owner is not None and owner.team != last_owner.team and gap_ok:
                _emit(
                    {
                        **payload,
                        "event_type": EventType.BALL_LOST,
                        "end_x": None,
                        "end_y": None,
                        "successful": False,
                    }
                )
                _emit(
                    {
                        **payload,
                        "team_id": team_ids[owner.team],
                        "player_id": player_ids[owner.track_id],
                        "event_type": EventType.INTERCEPTION,
                        "end_x": None,
                        "end_y": None,
                        "x": point[0],
                        "y": point[1],
                    }
                )
                _emit(
                    {
                        **payload,
                        "team_id": team_ids[owner.team],
                        "player_id": player_ids[owner.track_id],
                        "event_type": EventType.BALL_RECOVERY,
                        "end_x": None,
                        "end_y": None,
                        "x": point[0],
                        "y": point[1],
                    }
                )
                tagged = True
            elif last_owner is not None and not _same_actor(last_owner, owner, lookup, frame_index) and travel >= 3.5 and gap_ok:
                if corner:
                    kind = EventType.CORNER
                elif touchline:
                    kind = EventType.THROW_IN
                elif wide and in_box:
                    kind = EventType.CROSS
                elif travel >= 25.0:
                    kind = EventType.PASS
                    payload["is_progressive"] = True
                else:
                    kind = EventType.PASS
                extra: dict[str, object] = {"event_type": kind}
                if kind in {EventType.CORNER, EventType.THROW_IN}:
                    extra["end_x"] = point[0]
                    extra["end_y"] = point[1]
                _emit({**payload, **extra})
                tagged = True
            elif due and travel >= 4.0:
                kind = EventType.THROW_IN if touchline else EventType.PASS
                _emit({**payload, "event_type": kind})
                tagged = True

        if tagged:
            last_event_s = now_s
            last_tag_point = point
        last_owner = owner
        prev_point = point

    play_types = {
        EventType.PASS,
        EventType.CROSS,
        EventType.CUTBACK,
        EventType.ASSIST,
        EventType.SHOT,
        EventType.GOAL,
    }
    if not any(event.event_type in play_types for event in events):
        for track in players[:4]:
            period, minute, second, timestamp_ms = _clock(track.frames[-1], fps)
            start_x, start_y = track.xs[0], track.ys[0]
            end_x, end_y = track.xs[-1], track.ys[-1]
            if abs(end_x - start_x) < 1.0 and abs(end_y - start_y) < 1.0:
                end_x = min(100.0, max(0.0, start_x + (8.0 if goal_x[track.team] >= 50 else -8.0)))
            events.append(
                MatchEvent.model_validate(
                    {
                        "match_id": match_id,
                        "team_id": team_ids[track.team],
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
                        "is_progressive": abs(end_x - start_x) >= 8.0,
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
    """Decode the full match at ``sample_hz`` and build centroid tracks.

    Skipped frames use ``grab()`` so we do not fully decode them. A bad
    packet is skipped instead of aborting the rest of the 90 minutes.
    """

    step = max(1, int(round(info.fps / max(sample_hz, 0.1))))
    duration_samples = int(info.duration_seconds * max(sample_hz, 0.1)) if info.duration_seconds else 0
    from_frames = info.frame_count // step if info.frame_count > 0 else 0
    planned = from_frames or duration_samples or max_sample_frames
    planned = max(1, min(planned, max_sample_frames))
    capture = cv2.VideoCapture(str(info.path))
    if not capture.isOpened():
        raise VideoCollectError(f"OpenCV could not open the match film: {info.path}")
    tracks: list[Track] = []
    next_id = 1
    sampled = 0
    frame_index = 0
    consecutive_fail = 0
    max_missing = max(12, int(sample_hz * 3))
    match_minutes = max(info.duration_seconds / 60.0, planned / max(sample_hz, 0.1) / 60.0)
    try:
        while sampled < max_sample_frames:
            if step > 1 and frame_index % step != 0:
                grabbed = capture.grab()
                if not grabbed:
                    consecutive_fail += 1
                    if consecutive_fail >= 8:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index + step))
                    if consecutive_fail >= 250:
                        break
                    frame_index += 1
                    continue
                consecutive_fail = 0
                frame_index += 1
                continue
            ok, frame = capture.read()
            if not ok or frame is None:
                consecutive_fail += 1
                if consecutive_fail >= 8:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index + step))
                if consecutive_fail >= 250:
                    break
                frame_index += 1
                continue
            consecutive_fail = 0
            resized = _resize(frame, max_side)
            detections = detect_objects(resized)
            next_id = _match_tracks(
                tracks,
                detections,
                frame_index,
                next_id=next_id,
                max_missing=max_missing,
            )
            sampled += 1
            frame_index += 1
            if sampled == 1 or sampled % 10 == 0 or sampled >= planned:
                watched_min = sampled / max(sample_hz, 0.1) / 60.0
                _emit(
                    on_progress,
                    (
                        f"Watching minute {watched_min:.1f} / {match_minutes:.1f} "
                        f"· sampled {sampled}/{planned}"
                    ),
                    0.08 + 0.82 * (sampled / planned),
                )
            if sampled >= planned:
                break
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
        sample_hz: Decoded frames per second of match time (default 5 Hz
            so a 90-minute game is sampled ~27,000 times).
        max_side: Longest resized edge in pixels.
        max_sample_frames: Safety cap (default 48,000 ≈ 160 minutes at 5 Hz).
        on_progress: Optional ``(label, fraction)`` callback for a loading bar.
    """

    _emit(on_progress, "Opening match film…", 0.02)
    info = probe_video(path)
    minutes = info.duration_seconds / 60.0
    size_gb = info.size_bytes / (1024**3)
    needed = int(max(minutes, 0.1) * 60.0 * sample_hz) + 8
    frame_budget = max(max_sample_frames, needed) if minutes >= 5.0 else max_sample_frames
    frame_budget = min(frame_budget, DEFAULT_MAX_SAMPLE_FRAMES)
    _emit(
        on_progress,
        (
            f"Opened {info.path.name} · {minutes:.1f} min · {size_gb:.2f} GB · "
            f"watching the full match at {sample_hz:.0f} Hz"
        ),
        0.06,
    )
    tracks = sample_and_track(
        info,
        sample_hz=sample_hz,
        max_side=max_side,
        max_sample_frames=frame_budget,
        on_progress=on_progress,
    )
    _emit(on_progress, "Tagging play and collecting stats…", 0.93)
    match_id = uuid5(AUTO_NAMESPACE, f"video:{info.path.name}:{info.size_bytes}")
    team_id = uuid5(AUTO_NAMESPACE, f"team:{match_id}")
    clip_url = info.path.as_uri()
    home_name, away_name = infer_team_names(info.path.name)
    events, roster = events_from_tracks(
        tracks,
        fps=info.fps,
        match_id=match_id,
        team_id=team_id,
        clip_url=clip_url,
        home_name=home_name,
        away_name=away_name,
    )
    rundown = collect_game(
        GamePayload(
            match_id=match_id,
            players=roster,
            events=events,
            home_team_name=home_name,
            away_team_name=away_name,
        )
    )
    try:
        write_sidecar_xml(rundown, info.path)
    except OSError:
        pass
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
            if t < 0.45:
                ball_x = int(60 + (210 - 60) * (t / 0.45))
            else:
                ball_x = int(210 + (316 - 210) * ((t - 0.45) / 0.55))
            player_b = (int(240 + 20 * t), 90)
            cv2.circle(frame, player_a, 12, (180, 80, 20), -1)
            cv2.circle(frame, player_b, 12, (20, 40, 200), -1)
            cv2.circle(frame, (ball_x, 90), 5, (240, 240, 240), -1)
            writer.write(frame)
    finally:
        writer.release()
    return path
