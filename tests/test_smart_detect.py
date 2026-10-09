"""Tests for smarter film watching (kit colours + detectors)."""

from __future__ import annotations

from analytics.smart_detect import (
    assign_side_by_kit,
    bgr_distance,
    detect_people_hog,
    detector_label,
    hog_available,
    parse_kit_hex,
    yolo_available,
)
from analytics.video_auto_collect import (
    Track,
    _assign_teams,
    collect_from_video,
    write_synthetic_match_clip,
)


def test_parse_kit_hex_round_trip() -> None:
    assert parse_kit_hex("#1e3a8a") == (138.0, 58.0, 30.0)  # BGR
    assert parse_kit_hex("dc2626") == (38.0, 38.0, 220.0)
    assert parse_kit_hex("") is None
    assert parse_kit_hex("zz") is None


def test_assign_side_by_kit_picks_nearest() -> None:
    home = parse_kit_hex("#1e3a8a")
    away = parse_kit_hex("#dc2626")
    assert home is not None and away is not None
    assert assign_side_by_kit(home, home, away) == 0
    assert assign_side_by_kit(away, home, away) == 1
    assert assign_side_by_kit((0.0, 0.0, 0.0), None, away) is None


def test_assign_teams_uses_kit_colours() -> None:
    # Use the exact synthetic circle colours.
    home_exact = (180.0, 80.0, 20.0)
    away_exact = (20.0, 40.0, 200.0)
    players = [
        Track(track_id=1, kind="player", xs=[20.0], ys=[50.0], frames=[0], bgr=home_exact),
        Track(track_id=2, kind="player", xs=[80.0], ys=[50.0], frames=[0], bgr=away_exact),
        Track(track_id=3, kind="player", xs=[25.0], ys=[55.0], frames=[0], bgr=home_exact),
    ]
    _assign_teams(players, home_kit_bgr=home_exact, away_kit_bgr=away_exact)
    assert players[0].team == 0
    assert players[1].team == 1
    assert players[2].team == 0
    assert bgr_distance(home_exact, away_exact) > 50


def test_detector_label_mentions_finder() -> None:
    label = detector_label()
    assert "finder" in label.lower()
    assert isinstance(yolo_available(), bool)


def test_hog_missing_does_not_raise(monkeypatch) -> None:
    import analytics.smart_detect as sd

    monkeypatch.setattr(sd.cv2, "HOGDescriptor", None, raising=False)
    # Simulate OpenCV 5 builds that omit HOG entirely.
    if hasattr(sd.cv2, "HOGDescriptor"):
        monkeypatch.delattr(sd.cv2, "HOGDescriptor", raising=False)
    assert hog_available() is False
    import numpy as np

    assert detect_people_hog(np.zeros((48, 48, 3), dtype=np.uint8)) == []
    assert "blob" in detector_label().lower() or "yolo" in detector_label().lower()


def test_film_collect_with_kit_colours(tmp_path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "kits.avi")
    rundown = collect_from_video(
        clip,
        sample_hz=8.0,
        max_sample_frames=24,
        home_kit_hex="#B45014",
        away_kit_hex="#C82814",
        home_team_name="Pisa",
        away_team_name="Perugia",
    )
    assert rundown.summary.home_team_name == "Pisa"
    assert rundown.summary.away_team_name == "Perugia"
    assert rundown.events
    assert (tmp_path / "kits_Home.xml").is_file()
