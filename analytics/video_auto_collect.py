"""Auto-collect player stats from a match film — no manual event tags.

The operator uploads (or points at) a video up to 5 GB. This module:

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

from analytics.block_coverage import (
    BLOCK_MIN_EVENTS,
    BLOCK_SECONDS,
    MAX_REPASS_ROUNDS,
    block_index_for_clock,
    blocks_needing_repass,
    build_coverage_report,
    coverage_as_dicts,
    event_clock_seconds,
    expected_block_count,
    filter_events_outside_block,
)
from analytics.game_ingest import GamePayload, MatchRundown, PlayerRosterEntry, collect_game
from analytics.lineups import MatchLineups, LineupPlayer
from analytics.match_tags import infer_team_names, write_sidecar_xml
from analytics.oncesport_export import write_oncesport_pair
from analytics.smart_detect import (
    assign_side_by_kit,
    detect_objects_smart,
    detector_label,
    parse_kit_hex,
)
from analytics.tactics_iq import (
    GOAL_MIN_GAP_S,
    PENDING_SHOT_TTL_S,
    SHOT_MIN_GAP_S,
    classify_distribution,
    classify_strike,
    is_goalkeeper_actor,
    progressive_from_half_space,
    shot_outcome_for,
)
from data_models.events import EventType, MatchEvent, ShotOutcome

AUTO_NAMESPACE: UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
MAX_VIDEO_GIB: int = 5
MAX_VIDEO_BYTES: int = MAX_VIDEO_GIB * 1024 * 1024 * 1024
STREAMLIT_MAX_UPLOAD_MB: int = MAX_VIDEO_GIB * 1024
DEFAULT_SAMPLE_HZ: float = 8.0
DEFAULT_MAX_SIDE: int = 960
DEFAULT_MAX_SAMPLE_FRAMES: int = 72_000
# Arsenal v Palace (1-1) Wyscout analysis: 736 actions over 97.5 minutes.
WYSCOUT_ACTIONS_PER_MINUTE: float = 7.55
TARGET_EVENT_GAP_S: float = 60.0 / WYSCOUT_ACTIONS_PER_MINUTE
MIN_EVENT_GAP_S: float = 1.2
VIDEO_SUFFIXES: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")
TAG_SUFFIXES: tuple[str, ...] = (".xml",)
# Junk probe bytes and empty uploads must never become "Ready" films.
MIN_READY_VIDEO_BYTES = 256 * 1024
_VIDEO_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"\x00\x00\x00",  # ISO BMFF / MP4 (size + ftyp)
    b"ftyp",
    b"\x1aE\xdf\xa3",  # Matroska / WebM
    b"RIFF",  # AVI
)


def video_limit_label(max_bytes: int = MAX_VIDEO_BYTES) -> str:
    """Human-readable film size cap (``5 GB``, ``4.0 MB``, or raw bytes)."""

    if max_bytes >= 1024**3 and max_bytes % (1024**3) == 0:
        return f"{max_bytes // (1024 ** 3)} GB"
    if max_bytes >= 1024**2:
        return f"{max_bytes / (1024 ** 2):.1f} MB"
    return f"{max_bytes} bytes"


FILM_CHUNK_BYTES: int = 8 * 1024 * 1024
REMUX_COPY_TIMEOUT_S: int = 600
REMUX_ENCODE_TIMEOUT_S: int = 3600
ProgressFn = Callable[[str, float], None]


class VideoCollectError(ValueError):
    """Raised when a match film cannot be opened, is too large, or yields no play."""


def film_checkpoint_path(film: Path) -> Path:
    """Sidecar JSON that holds tagged events before the final stats fold."""

    resolved = film.expanduser().resolve()
    return resolved.with_name(f"{resolved.stem}.collect.checkpoint.json")


def write_collect_checkpoint(
    film: Path,
    *,
    match_id: UUID,
    home_name: str,
    away_name: str,
    events: Sequence[MatchEvent],
    roster: Sequence[PlayerRosterEntry],
    stage: str,
) -> Path:
    """Persist events/roster so a final-fold crash can finish without re-watching."""

    import json

    path = film_checkpoint_path(film)
    payload = {
        "film": str(film.expanduser().resolve()),
        "stage": stage,
        "match_id": str(match_id),
        "home_team_name": home_name,
        "away_team_name": away_name,
        "tag_source": "film",
        "players": [entry.model_dump(mode="json") for entry in roster],
        "events": [event.model_dump(mode="json") for event in events],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path


def load_collect_checkpoint(film: Path) -> dict[str, object] | None:
    """Load a checkpoint for ``film`` when present and matching."""

    import json

    path = film_checkpoint_path(film)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("film") or "") != str(film.expanduser().resolve()):
        return None
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        return None
    return payload


def finish_collect_from_checkpoint(film: Path) -> MatchRundown:
    """Fold a saved event sheet into the rundown — no film watch."""

    payload = load_collect_checkpoint(film)
    if payload is None:
        raise VideoCollectError(
            f"No saved tag sheet to finish for {film.name}. "
            "The previous analyse died before writing a checkpoint."
        )
    rundown = collect_game(
        GamePayload(
            match_id=UUID(str(payload["match_id"])),
            players=[
                PlayerRosterEntry.model_validate(row)  # type: ignore[arg-type]
                for row in payload["players"]
            ],
            events=[
                MatchEvent.model_validate(row)  # type: ignore[arg-type]
                for row in payload["events"]
            ],
            home_team_name=str(payload.get("home_team_name") or "Home"),
            away_team_name=str(payload.get("away_team_name") or "Away"),
            tag_source="film",
        )
    )
    info_path = film.expanduser().resolve()
    try:
        write_sidecar_xml(rundown, info_path)
    except OSError:
        pass
    try:
        write_oncesport_pair(
            rundown,
            info_path.parent,
            stem=info_path.stem,
            video_path=str(info_path),
        )
    except OSError:
        pass
    return rundown


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


def _looks_like_video_bytes(path: Path) -> bool:
    """Cheap header check so ASCII probe files never get analysed."""

    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < MIN_READY_VIDEO_BYTES:
        return False
    try:
        head = path.read_bytes()[:64]
    except OSError:
        return False
    if b"ftyp" in head[:32]:
        return True
    return any(head.startswith(magic) for magic in _VIDEO_MAGIC_PREFIXES)


def film_is_seekable(path: Path) -> bool:
    """Return whether the film body matches the duration claimed in its header.

    Truncated uploads (``.part`` renamed early) often open and report a full
    duration from the ``moov`` atom, then hang forever on ``partial file``
    seeks. If seeking near the end clamps far earlier than claimed, reject.
    """

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        return False
    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ok, _frame = capture.read()
        if not ok:
            return False
        if frame_count <= 100:
            return True
        target = max(0, frame_count - 25)
        capture.set(cv2.CAP_PROP_POS_FRAMES, float(target))
        pos = float(capture.get(cv2.CAP_PROP_POS_FRAMES) or 0.0)
        # Truncated mdat: OpenCV clamps the position well before the claimed end.
        if pos < frame_count * 0.5:
            return False
        ok, _frame = capture.read()
        return bool(ok)
    finally:
        capture.release()


def promote_finished_part_films(directory: Path) -> list[Path]:
    """Rename ``*.mp4.part`` only when the bytes are a seekable finished film.

    Chunked uploads write ``name.mp4.part`` and rename on the final chunk.
    Do **not** promote a partial body just because the MP4 header looks valid.
    """

    promoted: list[Path] = []
    if not directory.is_dir():
        return promoted
    for part in directory.glob("*.part"):
        if not part.is_file():
            continue
        name = part.name
        if not name.endswith(".part"):
            continue
        stem = name[: -len(".part")]
        suffix = Path(stem).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            continue
        dest = part.with_name(stem)
        if not _looks_like_video_bytes(part):
            continue
        if not film_is_seekable(part):
            continue
        try:
            if dest.exists():
                if dest.stat().st_size >= part.stat().st_size and film_is_seekable(dest):
                    part.unlink(missing_ok=True)
                    continue
                dest.unlink(missing_ok=True)
            part.replace(dest)
            promoted.append(dest)
        except OSError:
            continue
    return promoted


def list_ready_films(*directories: Path) -> list[Path]:
    """Newest-first films and tag XML sitting in the inbox / uploads folders."""

    found: list[Path] = []
    seen: set[Path] = set()
    search = directories or (film_inbox_dir(), film_upload_dir())
    accepted = VIDEO_SUFFIXES + TAG_SUFFIXES
    for directory in search:
        if not directory.is_dir():
            continue
        promote_finished_part_films(directory)
        for candidate in directory.iterdir():
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen or not resolved.is_file():
                continue
            suffix = resolved.suffix.lower()
            if suffix not in accepted:
                continue
            try:
                size = resolved.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            if suffix in VIDEO_SUFFIXES:
                if size < MIN_READY_VIDEO_BYTES or not _looks_like_video_bytes(resolved):
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
                raise VideoCollectError(
                    f"Match film exceeds the {video_limit_label(max_bytes)} upload limit."
                )
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
            "clip as mp4/avi. If this path is a tiny leftover file, "
            "re-upload the real match film and wait until it says Saved."
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
    jersey: int | None = None


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
    jersey: int | None = None
    jersey_votes: list[int] = field(default_factory=list)


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
            f"Match film is {size_bytes / (1024 ** 3):.2f} GB; "
            f"the limit is {video_limit_label()}."
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
    if not film_is_seekable(resolved):
        raise VideoCollectError(
            f"Match film looks incomplete or truncated: {resolved.name}. "
            "The upload probably stopped before it said Saved. "
            "Upload the full MP4 again, wait for Saved, then Analyse Stats."
        )
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
    """Find players/ball — smart HOG/YOLO first, pitch-blob fallback."""

    smart = detect_objects_smart(frame, blob_fallback=_detect_objects_blob)
    return [
        Detection(
            x=hit.x,
            y=hit.y,
            area=hit.area,
            kind=hit.kind,
            bgr=hit.bgr,
            jersey=hit.jersey,
        )
        for hit in smart
    ]


def _detect_objects_blob(frame: np.ndarray) -> list[Detection]:
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
        if detection.jersey is not None:
            track.jersey_votes.append(detection.jersey)
            # Majority vote keeps a stable shirt number.
            votes = track.jersey_votes[-12:]
            track.jersey = max(set(votes), key=votes.count)
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
                jersey=detection.jersey,
                jersey_votes=[detection.jersey] if detection.jersey is not None else [],
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


def _assign_teams(
    players: list[Track],
    *,
    home_kit_bgr: tuple[float, float, float] | None = None,
    away_kit_bgr: tuple[float, float, float] | None = None,
) -> None:
    """Split tracks into Home/Away using kit colours, then hue, then pitch X."""

    if len(players) < 2:
        for track in players:
            track.team = 0
        return

    # Step B: operator kit colours win when both sides are set.
    if home_kit_bgr is not None and away_kit_bgr is not None:
        assigned = 0
        for track in players:
            side = assign_side_by_kit(track.bgr, home_kit_bgr, away_kit_bgr)
            if side is not None:
                track.team = side
                assigned += 1
        if assigned >= max(2, len(players) // 2):
            # Fill leftovers by nearest kit.
            for track in players:
                if assign_side_by_kit(track.bgr, home_kit_bgr, away_kit_bgr) is not None:
                    continue
                d_home = abs(_hue(track.bgr) - _hue(home_kit_bgr))
                d_away = abs(_hue(track.bgr) - _hue(away_kit_bgr))
                track.team = 0 if d_home <= d_away else 1
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
        jersey=track.jersey,
        jersey_votes=list(track.jersey_votes),
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
        if track.jersey is not None:
            best.jersey_votes.extend(track.jersey_votes or [track.jersey])
            votes = best.jersey_votes[-12:]
            best.jersey = max(set(votes), key=votes.count)
    return identities


def _xy_at(
    track: Track,
    frame_index: int,
    lookup: dict[int, dict[int, tuple[float, float]]],
) -> tuple[float, float] | None:
    return lookup.get(track.track_id, {}).get(frame_index)


def _same_actor(
    left: Track,
    right: Track,
    lookup: dict[int, dict[int, tuple[float, float]]],
    frame_index: int,
) -> bool:
    if left.track_id == right.track_id:
        return True
    if left.team != right.team:
        return False
    a = _xy_at(left, frame_index, lookup)
    b = _xy_at(right, frame_index, lookup)
    if a is None or b is None:
        return math.hypot(left.last_x - right.last_x, left.last_y - right.last_y) <= 10.0
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= 10.0


def _position_from_depth(depth: float) -> str:
    if depth < 20:
        return "GK"
    if depth < 40:
        return "CB"
    if depth < 66:
        return "CM"
    return "ST"


def _assign_lineup_roster(
    players: list[Track],
    *,
    match_id: UUID,
    team_ids: tuple[UUID, UUID],
    home_name: str,
    away_name: str,
    goal_x: tuple[float, float],
    lineups: MatchLineups | None,
) -> tuple[list[PlayerRosterEntry], dict[int, UUID]]:
    """Build roster from OCR jersey + line-up sheet, else invented labels."""

    roster: list[PlayerRosterEntry] = []
    player_ids: dict[int, UUID] = {}
    used: dict[int, set[int]] = {0: set(), 1: set()}

    def _claim_lineup(
        side: str, team: int, preferred_pos: str, jersey_hint: int | None
    ) -> LineupPlayer | None:
        if lineups is None:
            return None
        sheet = lineups.for_side(side)
        if jersey_hint is not None:
            hit = lineups.by_jersey(side, jersey_hint)
            if hit is not None and hit.jersey not in used[team]:
                used[team].add(hit.jersey)
                return hit
        # Prefer matching position, then first unused.
        for player in sheet:
            if player.jersey in used[team]:
                continue
            if player.position and player.position == preferred_pos:
                used[team].add(player.jersey)
                return player
        for player in sheet:
            if player.jersey not in used[team]:
                used[team].add(player.jersey)
                return player
        return None

    fallback_jersey = {0: 1, 1: 1}
    for track in players:
        mean_x = sum(track.xs) / len(track.xs)
        attack_x = goal_x[track.team]
        depth = mean_x if attack_x >= 50 else 100.0 - mean_x
        position = _position_from_depth(depth)
        side = "home" if track.team == 0 else "away"
        team_name = home_name if track.team == 0 else away_name
        claimed = _claim_lineup(side, track.team, position, track.jersey)
        if claimed is not None:
            jersey = claimed.jersey
            name = claimed.name
            position = claimed.position or position
        else:
            jersey = (
                track.jersey if track.jersey is not None else min(fallback_jersey[track.team], 99)
            )
            fallback_jersey[track.team] = max(fallback_jersey[track.team], jersey) + 1
            while jersey in used[track.team] and jersey < 99:
                jersey += 1
            used[track.team].add(jersey)
            name = f"{team_name} {position} {jersey}"
        player_id = uuid5(AUTO_NAMESPACE, f"{match_id}-player-{track.track_id}")
        player_ids[track.track_id] = player_id
        roster.append(
            PlayerRosterEntry(
                player_id=player_id,
                team_id=team_ids[track.team],
                jersey_number=jersey,
                player_name=name,
                position=position,
            )
        )
    return roster, player_ids


def events_from_tracks(
    tracks: Sequence[Track],
    *,
    fps: float,
    match_id: UUID,
    team_id: UUID,
    clip_url: str,
    home_name: str = "Home",
    away_name: str = "Away",
    home_kit_bgr: tuple[float, float, float] | None = None,
    away_kit_bgr: tuple[float, float, float] | None = None,
    lineups: MatchLineups | None = None,
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
                key=lambda track: math.hypot(
                    track.xs[-1] - track.xs[0],
                    track.ys[-1] - track.ys[0],
                ),
            )
            players = [track for track in players if track is not ball]
    if not players:
        raise VideoCollectError(
            "No players found in the film. Try a clearer tactical / broadcast view."
        )
    _assign_teams(players, home_kit_bgr=home_kit_bgr, away_kit_bgr=away_kit_bgr)
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

    roster, player_ids = _assign_lineup_roster(
        players,
        match_id=match_id,
        team_ids=team_ids,
        home_name=home_name,
        away_name=away_name,
        goal_x=goal_x,
        lineups=lineups,
    )
    locked_team_by_player = {entry.player_id: entry.team_id for entry in roster}
    position_by_player = {entry.player_id: (entry.position or "") for entry in roster}
    name_by_player = {entry.player_id: (entry.player_name or "") for entry in roster}

    frame_indexes = sorted({frame for track in players for frame in track.frames})
    if ball is not None:
        frame_indexes = sorted(set(frame_indexes) | set(ball.frames))

    events: list[MatchEvent] = []
    last_event_s = -1e9
    last_shot_s = -1e9
    last_goal_s = -1e9
    last_tag_point: tuple[float, float] | None = None
    last_owner: Track | None = None
    prev_point: tuple[float, float] | None = None
    pending_shot: MatchEvent | None = None
    pending_shot_s = -1e9

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
        if pending_shot is not None and (now_s - pending_shot_s) > PENDING_SHOT_TTL_S:
            pending_shot = None
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
        touchline = point[1] <= 4.0 or point[1] >= 96.0
        corner = (point[0] <= 6.0 or point[0] >= 94.0) and (point[1] <= 10.0 or point[1] >= 90.0)
        gap_ok = (now_s - last_event_s) >= MIN_EVENT_GAP_S
        due = (now_s - last_event_s) >= TARGET_EVENT_GAP_S
        shot_gap_ok = (now_s - last_shot_s) >= SHOT_MIN_GAP_S
        goal_gap_ok = (now_s - last_goal_s) >= GOAL_MIN_GAP_S
        actor = last_owner if last_owner is not None else owner
        actor_player_id = player_ids[actor.track_id]
        actor_is_gk = is_goalkeeper_actor(
            position_by_player.get(actor_player_id),
            player_name=name_by_player.get(actor_player_id),
        )
        verdict = classify_strike(
            start=start,
            point=point,
            travel=travel,
            speed=speed,
            attack_goal_x=attack_goal,
            shot_gap_ok=shot_gap_ok,
            goal_gap_ok=goal_gap_ok,
            gap_ok=gap_ok,
            actor_is_gk=actor_is_gk,
            pending_shot=pending_shot is not None,
            pending_fresh=(now_s - pending_shot_s) <= PENDING_SHOT_TTL_S,
        )
        is_progressive = toward_goal and travel >= 8.0
        if progressive_from_half_space(
            start_y=start[1], toward_goal=toward_goal, travel=travel
        ):
            is_progressive = True
        payload: dict[str, object] = {
            "match_id": match_id,
            "team_id": locked_team_by_player.get(actor_player_id, team_ids[actor.team]),
            "player_id": actor_player_id,
            "period": period,
            "minute": minute,
            "second": second,
            "x": start[0],
            "y": start[1],
            "end_x": point[0],
            "end_y": point[1],
            "successful": True,
            "is_progressive": is_progressive,
            "attacking_left_to_right": attack_goal >= 50,
            "video_timestamp_ms": timestamp_ms,
            "clip_url": clip_url,
        }

        tagged = False
        if verdict.is_goal:
            _emit(
                {
                    **payload,
                    "event_type": EventType.GOAL,
                    "is_goal": True,
                    "shot_outcome": ShotOutcome.ON_TARGET,
                    "player_id": (
                        pending_shot.player_id if pending_shot is not None else actor_player_id
                    ),
                    "team_id": (
                        pending_shot.team_id
                        if pending_shot is not None
                        else payload["team_id"]
                    ),
                }
            )
            pending_shot = None
            last_shot_s = now_s
            last_goal_s = now_s
            tagged = True
        elif verdict.is_shot:
            shot = _emit(
                {
                    **payload,
                    "event_type": EventType.SHOT,
                    "is_goal": False,
                    "shot_outcome": shot_outcome_for(verdict),
                }
            )
            pending_shot = shot
            pending_shot_s = now_s
            last_shot_s = now_s
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
                        "event_type": (
                            EventType.AERIAL_DUEL if clustered >= 4 else EventType.GROUND_DUEL
                        ),
                        "end_x": None,
                        "end_y": None,
                        "successful": True,
                    }
                )
                tagged = True
            elif last_owner is not None and owner.team != last_owner.team and gap_ok:
                # Defensive transition → attacking transition for the winner.
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
            elif (
                last_owner is not None
                and not _same_actor(last_owner, owner, lookup, frame_index)
                and travel >= 3.5
                and gap_ok
            ):
                kind = classify_distribution(
                    start=start,
                    point=point,
                    travel=travel,
                    toward_goal=toward_goal,
                    in_box=in_box,
                    touchline=touchline,
                    corner=corner,
                    actor_is_gk=actor_is_gk,
                )
                if kind is EventType.PASS and travel >= 30.0:
                    payload["is_progressive"] = True
                elif kind is EventType.PASS and travel >= 18.0 and toward_goal:
                    payload["is_progressive"] = True
                extra: dict[str, object] = {"event_type": kind}
                if kind in {EventType.CORNER, EventType.THROW_IN}:
                    extra["end_x"] = point[0]
                    extra["end_y"] = point[1]
                _emit({**payload, **extra})
                tagged = True
            elif due and travel >= 4.0:
                kind = classify_distribution(
                    start=start,
                    point=point,
                    travel=travel,
                    toward_goal=toward_goal,
                    in_box=in_box,
                    touchline=touchline,
                    corner=False,
                    actor_is_gk=actor_is_gk,
                )
                if kind is EventType.CROSS and actor_is_gk:
                    kind = EventType.PASS
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
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
) -> list[Track]:
    """Decode match film at ``sample_hz`` and build centroid tracks.

    Optional ``start_seconds`` / ``end_seconds`` limit the window (Step C
    re-pass). Skipped frames use ``grab()`` so we do not fully decode them.
    """

    window_end = (
        float(end_seconds)
        if end_seconds is not None
        else (info.duration_seconds or float(max_sample_frames) / max(sample_hz, 0.1))
    )
    window_start = max(0.0, float(start_seconds))
    window_end = max(window_start + 0.5, window_end)
    window_duration = max(0.5, window_end - window_start)

    step = max(1, int(round(info.fps / max(sample_hz, 0.1))))
    planned = int(window_duration * max(sample_hz, 0.1)) + 2
    planned = max(1, min(planned, max_sample_frames))
    capture = cv2.VideoCapture(str(info.path))
    if not capture.isOpened():
        raise VideoCollectError(f"OpenCV could not open the match film: {info.path}")
    tracks: list[Track] = []
    next_id = 1
    sampled = 0
    consecutive_fail = 0
    max_missing = max(12, int(sample_hz * 3))
    start_frame = int(round(window_start * max(info.fps, 0.01)))
    end_frame = int(round(window_end * max(info.fps, 0.01)))
    frame_index = start_frame
    if start_frame > 0:
        capture.set(cv2.CAP_PROP_POS_FRAMES, float(start_frame))
    try:
        while sampled < max_sample_frames and frame_index < end_frame:
            if step > 1 and (frame_index - start_frame) % step != 0:
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
                watched_min = (frame_index / max(info.fps, 0.01)) / 60.0
                _emit(
                    on_progress,
                    (f"Watching minute {watched_min:.1f} " f"· sampled {sampled}/{planned}"),
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
    home_kit_hex: str | None = None,
    away_kit_hex: str | None = None,
    home_team_name: str | None = None,
    away_team_name: str | None = None,
    lineups: MatchLineups | None = None,
) -> MatchRundown:
    """Watch a match film and return the collected four-pillar rundown.

    Args:
        path: Local path to an mp4/mov/mkv/avi file, at most 5 GB.
        sample_hz: Decoded frames per second of match time.
        max_side: Longest resized edge in pixels.
        max_sample_frames: Safety cap.
        on_progress: Optional ``(label, fraction)`` callback for a loading bar.
        home_kit_hex: Optional ``#RRGGBB`` home shirt colour for team split.
        away_kit_hex: Optional ``#RRGGBB`` away shirt colour for team split.
        home_team_name: Optional home display name (else inferred from filename).
        away_team_name: Optional away display name.
        lineups: Optional real Home/Away sheets (names + shirt numbers).
    """

    # A prior watch that died at the final fold can finish here — no re-watch.
    prior = load_collect_checkpoint(path)
    if prior is not None and str(prior.get("stage") or "") == "pre_collect_game":
        _emit(on_progress, "Finishing from saved tags (no re-watch)…", 0.95)
        rundown = finish_collect_from_checkpoint(path)
        _emit(
            on_progress,
            (
                f"Rundown ready from checkpoint · {len(rundown.events)} events · "
                "Home/Away XML written"
            ),
            1.0,
        )
        return rundown

    home_kit_bgr = parse_kit_hex(home_kit_hex)
    away_kit_bgr = parse_kit_hex(away_kit_hex)
    _emit(on_progress, f"Opening match film… ({detector_label()})", 0.02)
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
            f"watching with {detector_label()} at {sample_hz:.0f} Hz"
            + (" · line-ups loaded" if lineups is not None else "")
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
    inferred_home, inferred_away = infer_team_names(info.path.name)
    if lineups is not None:
        home_name = (home_team_name or "").strip() or lineups.home_team or inferred_home
        away_name = (away_team_name or "").strip() or lineups.away_team or inferred_away
    else:
        home_name = (home_team_name or "").strip() or inferred_home
        away_name = (away_team_name or "").strip() or inferred_away
    events, roster = events_from_tracks(
        tracks,
        fps=info.fps,
        match_id=match_id,
        team_id=team_id,
        clip_url=clip_url,
        home_name=home_name,
        away_name=away_name,
        home_kit_bgr=home_kit_bgr,
        away_kit_bgr=away_kit_bgr,
        lineups=lineups,
    )
    write_collect_checkpoint(
        info.path,
        match_id=match_id,
        home_name=home_name,
        away_name=away_name,
        events=events,
        roster=roster,
        stage="after_first_pass",
    )
    duration_minutes = max(minutes, rundown_minutes_from_events(events), 0.1)
    home_team_id = uuid5(AUTO_NAMESPACE, f"{match_id}-team-{home_name}")
    coverage = build_coverage_report(
        events,
        duration_minutes,
        home_team_ids={home_team_id},
    )
    # Step C: re-watch thin 5-minute windows (up to MAX_REPASS_ROUNDS) denser.
    if info.duration_seconds >= BLOCK_SECONDS * 0.5:
        for round_no in range(1, MAX_REPASS_ROUNDS + 1):
            sparse = blocks_needing_repass(coverage)
            if not sparse:
                break
            denser_hz = min(sample_hz * (1.0 + round_no), 16.0)
            total_sparse = len(sparse)
            for order, block_index in enumerate(sparse):
                start_s = float(block_index * BLOCK_SECONDS)
                end_s = start_s + float(BLOCK_SECONDS)
                if start_s >= info.duration_seconds:
                    continue
                end_s = min(end_s, info.duration_seconds)
                _emit(
                    on_progress,
                    (
                        f"Re-pass round {round_no}/{MAX_REPASS_ROUNDS} · "
                        f"block {block_index + 1}/"
                        f"{expected_block_count(duration_minutes)} "
                        f"({order + 1}/{total_sparse}) at {denser_hz:.0f} Hz"
                    ),
                    0.82
                    + 0.12
                    * (
                        ((round_no - 1) + (order + 1) / max(total_sparse, 1))
                        / max(MAX_REPASS_ROUNDS, 1)
                    ),
                )
                try:
                    block_tracks = sample_and_track(
                        info,
                        sample_hz=denser_hz,
                        max_side=max_side,
                        max_sample_frames=min(
                            frame_budget,
                            int(BLOCK_SECONDS * denser_hz) + 16,
                        ),
                        start_seconds=start_s,
                        end_seconds=end_s,
                    )
                    block_events, block_roster = events_from_tracks(
                        block_tracks,
                        fps=info.fps,
                        match_id=match_id,
                        team_id=team_id,
                        clip_url=clip_url,
                        home_name=home_name,
                        away_name=away_name,
                        home_kit_bgr=home_kit_bgr,
                        away_kit_bgr=away_kit_bgr,
                        lineups=lineups,
                    )
                except VideoCollectError:
                    continue
                kept = filter_events_outside_block(events, block_index)
                fresh = [
                    event
                    for event in block_events
                    if block_index_for_clock(event_clock_seconds(event)) == block_index
                ]
                if not fresh:
                    fresh = block_events
                # Keep denser result only when it improves density.
                old_n = len(events) - len(kept)
                if len(fresh) >= old_n:
                    events = kept + fresh
                    roster_by_id = {entry.player_id: entry for entry in roster}
                    for entry in block_roster:
                        roster_by_id.setdefault(entry.player_id, entry)
                    roster = list(roster_by_id.values())
            coverage = build_coverage_report(
                events,
                duration_minutes,
                home_team_ids={home_team_id},
            )

    # Save tags BEFORE the final fold — if collect_game fails, we can finish
    # without re-watching the film.
    write_collect_checkpoint(
        info.path,
        match_id=match_id,
        home_name=home_name,
        away_name=away_name,
        events=events,
        roster=roster,
        stage="pre_collect_game",
    )
    _emit(on_progress, "Folding tags into the match sheet…", 0.95)
    try:
        rundown = collect_game(
            GamePayload(
                match_id=match_id,
                players=roster,
                events=events,
                home_team_name=home_name,
                away_team_name=away_name,
                tag_source="film",
            )
        )
    except ValueError as exc:
        # Kit-flip / fold bugs must not throw away a finished watch.
        try:
            rundown = finish_collect_from_checkpoint(info.path)
        except VideoCollectError:
            raise VideoCollectError(
                f"Final fold failed ({exc}). Tags are saved — click Analyse again "
                "to finish from the checkpoint without re-watching."
            ) from exc
    try:
        write_sidecar_xml(rundown, info.path)
    except OSError:
        pass
    try:
        write_oncesport_pair(
            rundown,
            info.path.parent,
            stem=info.path.stem,
            video_path=str(info.path),
        )
    except OSError:
        pass
    try:
        import json

        coverage_path = info.path.with_name(f"{info.path.stem}.coverage.json")
        coverage_path.write_text(
            json.dumps(
                {
                    "block_seconds": BLOCK_SECONDS,
                    "min_events": BLOCK_MIN_EVENTS,
                    "blocks": coverage_as_dicts(coverage),
                    "repass_blocks": [row.block_index + 1 for row in coverage if row.needs_repass],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    still_thin = sum(1 for row in coverage if row.needs_repass)
    _emit(
        on_progress,
        (
            f"Rundown ready · {len(coverage)}×5-min blocks · "
            f"{still_thin} still thin after re-pass · Home/Away XML written"
        ),
        1.0,
    )
    return rundown


def rundown_minutes_from_events(events: Sequence[MatchEvent]) -> float:
    """Best-effort match length from the last tagged clock."""

    if not events:
        return 0.0
    last = max(((event.period - 1) * 45 + event.minute + event.second / 60.0) for event in events)
    return float(last)


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
