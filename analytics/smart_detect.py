"""Smarter film watching: people finder + kit colours (no Grokbot).

Step A — find people better (HOG person finder, optional YOLO if installed,
blob fallback for synthetic / soft pitch).

Step B — split Home / Away by shirt colour when the operator gives kit hexes.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

import cv2
import numpy as np

# COCO class ids used by ultralytics YOLOv8 when available.
_YOLO_PERSON = 0
_YOLO_SPORTS_BALL = 32


from analytics.jersey_ocr import read_jersey_number

# OCR is slow — only try a few crops per process lifetime window.
_OCR_BUDGET = {"left": 40}


def _sparse_jersey(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> int | None:
    """Read a shirt number occasionally (not every detection)."""

    if _OCR_BUDGET["left"] <= 0:
        return None
    if (y2 - y1) < 32 or (x2 - x1) < 16:
        return None
    # Sample ~1 in 8 player boxes.
    if hash((x1, y1, x2, y2)) % 8 != 0:
        return None
    _OCR_BUDGET["left"] -= 1
    return read_jersey_number(frame[y1:y2, x1:x2])


@dataclass(frozen=True, slots=True)
class SmartDetection:
    """One body or ball on a frame, in 0–100 pitch coords."""

    x: float
    y: float
    area: float
    kind: str  # "player" | "ball"
    bgr: tuple[float, float, float]
    source: str = "blob"  # hog | yolo | blob
    jersey: int | None = None


def parse_kit_hex(raw: str | None) -> tuple[float, float, float] | None:
    """Parse ``#RRGGBB`` / ``RRGGBB`` into OpenCV BGR. Empty → None."""

    if raw is None:
        return None
    text = str(raw).strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        red = int(text[0:2], 16)
        green = int(text[2:4], 16)
        blue = int(text[4:6], 16)
    except ValueError:
        return None
    return (float(blue), float(green), float(red))


def bgr_distance(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    """Euclidean distance in BGR space."""

    return math.sqrt(
        (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2 + (left[2] - right[2]) ** 2
    )


def assign_side_by_kit(
    shirt_bgr: tuple[float, float, float],
    home_bgr: tuple[float, float, float] | None,
    away_bgr: tuple[float, float, float] | None,
) -> int | None:
    """Return 0=home, 1=away, or None if kits are missing / too close."""

    if home_bgr is None or away_bgr is None:
        return None
    if bgr_distance(home_bgr, away_bgr) < 12.0:
        return None
    d_home = bgr_distance(shirt_bgr, home_bgr)
    d_away = bgr_distance(shirt_bgr, away_bgr)
    if abs(d_home - d_away) < 8.0:
        return None
    return 0 if d_home <= d_away else 1


def _to_pitch(
    x_px: float,
    y_px: float,
    width: int,
    height: int,
    *,
    roi: tuple[int, int, int, int] | None = None,
) -> tuple[float, float]:
    from analytics.pitch_map import to_pitch

    return to_pitch(x_px, y_px, width, height, roi=roi)


def _mean_bgr_box(
    frame: np.ndarray, x1: int, y1: int, x2: int, y2: int
) -> tuple[float, float, float]:
    """Average colour of the upper torso band inside a person box."""

    height, width = frame.shape[:2]
    x1 = max(0, min(width - 1, x1))
    x2 = max(0, min(width, x2))
    y1 = max(0, min(height - 1, y1))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        return (0.0, 0.0, 0.0)
    # Shirt band: middle-upper third of the box (avoid head + legs).
    band_top = y1 + int((y2 - y1) * 0.20)
    band_bot = y1 + int((y2 - y1) * 0.55)
    if band_bot <= band_top:
        band_top, band_bot = y1, y2
    patch = frame[band_top:band_bot, x1:x2]
    if patch.size == 0:
        return (0.0, 0.0, 0.0)
    mean = patch.reshape(-1, 3).mean(axis=0)
    return (float(mean[0]), float(mean[1]), float(mean[2]))


def hog_available() -> bool:
    """OpenCV 4 has HOGDescriptor; OpenCV 5 builds often omit it."""

    return hasattr(cv2, "HOGDescriptor")


@lru_cache(maxsize=1)
def _hog_detector():  # type: ignore[no-untyped-def]
    if not hog_available():
        raise RuntimeError("cv2.HOGDescriptor is not available in this OpenCV build")
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return hog


def detect_people_hog(
    frame: np.ndarray,
    *,
    roi: tuple[int, int, int, int] | None = None,
) -> list[SmartDetection]:
    """OpenCV HOG people finder when the build includes it; else empty."""

    if frame.size == 0 or not hog_available():
        return []
    height, width = frame.shape[:2]
    try:
        hog = _hog_detector()
        # winStride / scale tuned for broadcast-ish frames (and tiny synthetic clips).
        boxes, weights = hog.detectMultiScale(
            frame,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.05,
        )
    except Exception:  # noqa: BLE001 — optional path must never break collect
        return []
    if roi is None:
        from analytics.pitch_map import grass_roi

        roi = grass_roi(frame)
    found: list[SmartDetection] = []
    for (bx, by, bw, bh), weight in zip(boxes, weights, strict=False):
        if float(weight) < 0.3:
            continue
        cx = bx + bw / 2.0
        cy = by + bh / 2.0
        x, y = _to_pitch(cx, cy, width, height, roi=roi)
        area = float(bw * bh)
        bgr = _mean_bgr_box(frame, int(bx), int(by), int(bx + bw), int(by + bh))
        jersey = _sparse_jersey(frame, int(bx), int(by), int(bx + bw), int(by + bh))
        found.append(
            SmartDetection(x=x, y=y, area=area, kind="player", bgr=bgr, source="hog", jersey=jersey)
        )
    return found[:22]


def yolo_available() -> bool:
    """True when ultralytics is installed and not disabled via env."""

    flag = os.environ.get("STATMAN_DISABLE_YOLO", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return False
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _yolo_model():  # type: ignore[no-untyped-def]
    from ultralytics import YOLO

    # Small nano weights — downloaded once if missing.
    return YOLO("yolov8n.pt")


def detect_people_yolo(
    frame: np.ndarray,
    *,
    roi: tuple[int, int, int, int] | None = None,
) -> list[SmartDetection]:
    """Optional YOLOv8 person + sports-ball detector."""

    if not yolo_available() or frame.size == 0:
        return []
    try:
        model = _yolo_model()
        results = model.predict(frame, verbose=False, conf=0.25)
    except Exception:  # noqa: BLE001 — optional path must never break collect
        return []
    height, width = frame.shape[:2]
    if roi is None:
        from analytics.pitch_map import grass_roi

        roi = grass_roi(frame)
    found: list[SmartDetection] = []
    for result in results:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            cls_id = int(box.cls[0]) if box.cls is not None else -1
            if cls_id not in {_YOLO_PERSON, _YOLO_SPORTS_BALL}:
                continue
            xyxy = box.xyxy[0].tolist()
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            x, y = _to_pitch(cx, cy, width, height, roi=roi)
            area = float(max(1, (x2 - x1) * (y2 - y1)))
            kind = "ball" if cls_id == _YOLO_SPORTS_BALL else "player"
            bgr = _mean_bgr_box(frame, x1, y1, x2, y2)
            jersey = _sparse_jersey(frame, x1, y1, x2, y2) if kind == "player" else None
            found.append(
                SmartDetection(
                    x=x, y=y, area=area, kind=kind, bgr=bgr, source="yolo", jersey=jersey
                )
            )
    players = [row for row in found if row.kind == "player"][:22]
    balls = [row for row in found if row.kind == "ball"]
    if balls:
        players.append(min(balls, key=lambda row: row.area))
    return players


def merge_detections(
    primary: Sequence[SmartDetection],
    secondary: Sequence[SmartDetection],
    *,
    min_distance: float = 4.0,
) -> list[SmartDetection]:
    """Keep primary hits; add secondary ones that are not near a primary."""

    merged = list(primary)
    for candidate in secondary:
        too_close = False
        for existing in merged:
            if existing.kind != candidate.kind:
                continue
            if math.hypot(existing.x - candidate.x, existing.y - candidate.y) < min_distance:
                too_close = True
                break
        if not too_close:
            merged.append(candidate)
    players = [row for row in merged if row.kind == "player"][:22]
    balls = [row for row in merged if row.kind == "ball"]
    if balls:
        players.append(min(balls, key=lambda row: row.area))
    return players


def detect_objects_smart(
    frame: np.ndarray,
    *,
    blob_fallback,  # Callable[[np.ndarray], list] — avoids circular imports
    roi: tuple[int, int, int, int] | None = None,
) -> list[SmartDetection]:
    """Best available detector: YOLO → HOG+blob → blob only."""

    blob_raw = blob_fallback(frame)
    blob_hits = [
        SmartDetection(
            x=float(item.x),
            y=float(item.y),
            area=float(item.area),
            kind=str(item.kind),
            bgr=tuple(float(v) for v in item.bgr),
            source="blob",
            jersey=getattr(item, "jersey", None),
        )
        for item in blob_raw
    ]
    if yolo_available():
        yolo_hits = detect_people_yolo(frame, roi=roi)
        if yolo_hits:
            return merge_detections(yolo_hits, blob_hits)

    if hog_available():
        hog_hits = detect_people_hog(frame, roi=roi)
        if hog_hits:
            return merge_detections(hog_hits, blob_hits)
    return blob_hits


def detector_label() -> str:
    """Short status string for the UI / progress bar."""

    if yolo_available():
        return "YOLO person finder"
    if hog_available():
        return "HOG person finder + pitch blobs"
    return "pitch blobs only"
