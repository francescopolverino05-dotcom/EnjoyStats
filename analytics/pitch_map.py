"""Map broadcast pixels onto the 0–100 pitch grid.

Full-frame linear mapping puts scoreboards/stands into the tag sheet.
We prefer the grass ROI when it is visible so x/y sit on the field.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np

PitchRoi = tuple[int, int, int, int]  # x0, y0, x1, y1 inclusive-ish
_MIN_ROI_FRAC: Final[float] = 0.12


def grass_roi(frame: np.ndarray) -> PitchRoi | None:
    """Bounding box of the largest grass region, or ``None`` if unclear."""

    if frame.size == 0:
        return None
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    grass = cv2.inRange(hsv, (18, 12, 25), (100, 255, 230))
    grass = cv2.bitwise_or(grass, cv2.inRange(hsv, (8, 12, 25), (40, 90, 230)))
    vivid = cv2.inRange(hsv, (35, 40, 40), (90, 255, 255))
    grass = cv2.bitwise_or(grass, vivid)
    closed = cv2.morphologyEx(grass, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    height, width = frame.shape[:2]
    frame_area = float(max(width * height, 1))
    if cv2.contourArea(largest) / frame_area < _MIN_ROI_FRAC:
        return None
    x, y, w, h = cv2.boundingRect(largest)
    if w < 32 or h < 32:
        return None
    # Shrink slightly so touchline chrome stays out of the grid.
    pad_x = max(2, w // 40)
    pad_y = max(2, h // 40)
    x0 = max(0, x + pad_x)
    y0 = max(0, y + pad_y)
    x1 = min(width - 1, x + w - pad_x)
    y1 = min(height - 1, y + h - pad_y)
    if x1 <= x0 + 16 or y1 <= y0 + 16:
        return None
    return (x0, y0, x1, y1)


def to_pitch(
    x_px: float,
    y_px: float,
    width: int,
    height: int,
    *,
    roi: PitchRoi | None = None,
) -> tuple[float, float]:
    """Map a pixel centroid onto FIFA 0–100 tagging coordinates."""

    if width <= 0 or height <= 0:
        return 50.0, 50.0
    if roi is not None:
        x0, y0, x1, y1 = roi
        span_x = max(1.0, float(x1 - x0))
        span_y = max(1.0, float(y1 - y0))
        x = ((x_px - x0) / span_x) * 100.0
        y = ((y_px - y0) / span_y) * 100.0
    else:
        x = (x_px / float(width)) * 100.0
        y = (y_px / float(height)) * 100.0
    return max(0.0, min(100.0, x)), max(0.0, min(100.0, y))
