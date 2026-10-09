"""Tests for grass-ROI pitch mapping and fresh re-analyse helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from analytics.pitch_map import grass_roi, to_pitch
from analytics.video_auto_collect import clear_film_collect_state, film_checkpoint_path


def test_to_pitch_full_frame() -> None:
    assert to_pitch(0, 0, 200, 100) == (0.0, 0.0)
    assert to_pitch(200, 100, 200, 100) == (100.0, 100.0)
    assert to_pitch(100, 50, 200, 100) == (50.0, 50.0)


def test_to_pitch_uses_grass_roi() -> None:
    # ROI is the middle half of the frame — a point at the ROI centre → 50,50.
    roi = (50, 25, 150, 75)
    x, y = to_pitch(100, 50, 200, 100, roi=roi)
    assert 49.0 <= x <= 51.0
    assert 49.0 <= y <= 51.0


def test_grass_roi_finds_green_field() -> None:
    frame = np.zeros((120, 200, 3), dtype=np.uint8)
    # BGR green rectangle = pitch.
    frame[20:100, 30:170] = (40, 160, 40)
    roi = grass_roi(frame)
    assert roi is not None
    x0, y0, x1, y1 = roi
    assert x1 - x0 > 80
    assert y1 - y0 > 40


def test_clear_film_collect_state_removes_checkpoint(tmp_path: Path) -> None:
    film = tmp_path / "match.mp4"
    film.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 300)
    checkpoint = film_checkpoint_path(film)
    checkpoint.write_text('{"events":[1]}', encoding="utf-8")
    coverage = film.with_name("match.coverage.json")
    coverage.write_text("{}", encoding="utf-8")
    removed = clear_film_collect_state(film)
    assert checkpoint.name in removed
    assert coverage.name in removed
    assert not checkpoint.exists()
    assert not coverage.exists()
