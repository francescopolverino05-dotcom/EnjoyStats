"""Spiideo-style tag XML and filename team inference."""

from __future__ import annotations

from analytics.match_tags import (
    collect_from_tag_xml,
    infer_team_names,
    rundown_to_xml,
    write_sidecar_xml,
)
from analytics.sample_game import sample_game_payload
from analytics.game_ingest import collect_game
from analytics.video_auto_collect import collect_from_video, write_synthetic_match_clip
from app.ingest import collect_from_film_path


def test_infer_team_names_from_broadcast_filename() -> None:
    home, away = infer_team_names("ascoli_-_spezia_v1__540p_.mp4")
    assert home == "Ascoli"
    assert away == "Spezia"
    home, away = infer_team_names("Avellino_-_Napoli__3-1_.mp4")
    assert home == "Avellino"
    assert away == "Napoli"


def test_sample_game_xml_roundtrip_keeps_goals_and_passes() -> None:
    rundown = collect_game(sample_game_payload())
    xml = rundown_to_xml(rundown)
    assert "<MatchTags" in xml
    assert "<Tags>" in xml
    assert "<Attacks>" in xml
    restored = collect_from_tag_xml(xml)
    assert restored.summary.goals == rundown.summary.goals
    assert restored.summary.passes == rundown.summary.passes
    assert restored.summary.shots == rundown.summary.shots
    assert restored.summary.player_count == rundown.summary.player_count


def test_sidecar_xml_recollects_from_path(tmp_path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "Ascoli_-_Spezia.avi", frames=32, fps=8)
    rundown = collect_from_video(clip, sample_hz=8.0, max_sample_frames=32)
    sidecar = write_sidecar_xml(rundown, clip)
    assert sidecar.name.endswith(".tags.xml")
    from_xml = collect_from_film_path(sidecar)
    assert from_xml.summary.event_count == rundown.summary.event_count
    assert from_xml.summary.passes + from_xml.summary.shots >= 1


def test_constructed_attack_track_tags_a_goal() -> None:
    from analytics.video_auto_collect import Track, events_from_tracks
    from data_models.events import EventType
    from uuid import uuid4

    attacker = Track(
        track_id=1,
        kind="player",
        xs=[40.0, 50.0, 70.0, 85.0],
        ys=[50.0, 50.0, 50.0, 50.0],
        frames=[0, 8, 16, 24],
        last_x=85.0,
        last_y=50.0,
        bgr=(20.0, 40.0, 200.0),
        team=0,
    )
    defender = Track(
        track_id=2,
        kind="player",
        xs=[20.0, 20.0, 22.0, 24.0],
        ys=[40.0, 41.0, 42.0, 40.0],
        frames=[0, 8, 16, 24],
        last_x=24.0,
        last_y=40.0,
        bgr=(200.0, 80.0, 20.0),
        team=1,
    )
    ball = Track(
        track_id=3,
        kind="ball",
        xs=[45.0, 62.0, 84.0, 97.0],
        ys=[50.0, 50.0, 50.0, 50.0],
        frames=[0, 8, 16, 24],
        last_x=97.0,
        last_y=50.0,
    )
    events, roster = events_from_tracks(
        [attacker, defender, ball],
        fps=8.0,
        match_id=uuid4(),
        team_id=uuid4(),
        clip_url="file:///tmp/clip.avi",
        home_name="Ascoli",
        away_name="Spezia",
    )
    assert any(event.is_goal or event.event_type is EventType.GOAL for event in events)
    assert any(event.event_type in {EventType.SHOT, EventType.GOAL} for event in events)
    assert any("Ascoli" in entry.player_name or "Spezia" in entry.player_name for entry in roster)


def test_synthetic_clip_tags_shots_or_passes(tmp_path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "Home_-_Away.avi", frames=32, fps=8)
    rundown = collect_from_video(clip, sample_hz=8.0, max_sample_frames=32)
    assert rundown.summary.event_count >= 1
    assert rundown.summary.passes + rundown.summary.shots + rundown.summary.goals >= 1
    names = {profile.player_name for profile in rundown.players}
    assert any("Home" in name or "Away" in name or "Player" in name for name in names)
    xml = rundown_to_xml(rundown)
    assert "type=" in xml
