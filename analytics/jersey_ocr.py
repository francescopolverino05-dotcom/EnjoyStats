"""Best-effort jersey number reading from player crops (Tesseract)."""

from __future__ import annotations

import re

import cv2
import numpy as np

_DIGIT_RE = re.compile(r"\b([1-9][0-9]?)\b")


def ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def read_jersey_number(crop_bgr: np.ndarray) -> int | None:
    """Read a shirt number from a small BGR crop. Returns 1–99 or None."""

    if crop_bgr is None or crop_bgr.size == 0:
        return None
    if not ocr_available():
        return None
    try:
        import pytesseract
    except ImportError:
        return None

    height, width = crop_bgr.shape[:2]
    if height < 8 or width < 8:
        return None
    # Focus on torso digits.
    y1 = int(height * 0.15)
    y2 = int(height * 0.65)
    x1 = int(width * 0.20)
    x2 = int(width * 0.80)
    patch = crop_bgr[y1:y2, x1:x2]
    if patch.size == 0:
        patch = crop_bgr
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Also try inverted (white numbers on dark shirts).
    candidates = [bw, cv2.bitwise_not(bw)]
    config = "--psm 8 -c tessedit_char_whitelist=0123456789"
    for image in candidates:
        try:
            text = pytesseract.image_to_string(image, config=config)
        except Exception:  # noqa: BLE001
            continue
        match = _DIGIT_RE.search(text or "")
        if not match:
            continue
        value = int(match.group(1))
        if 1 <= value <= 99:
            return value
    return None
