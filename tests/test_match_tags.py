"""Spiideo-style tag XML and filename team inference."""

from __future__ import annotations

from analytics.match_tags import (
    collect_from_tag_xml,
    find_official_tag_xml,
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
    home, away = infer_team_names("Arsenal v Palace (1-1).xml")
    assert home == "Arsenal"
    assert away == "Palace"
    home, away = infer_team_names("Arsenal_v_Palace__1-1__c4e3.xml")
    assert home == "Arsenal"
    assert away == "Palace"


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


_WYSCOUT_MINI = """<?xml version="1.0" encoding="utf-8"?>
<analysis id="9f3a2dfe-78c8-48bf-8e94-7483348ffb45" title="Arsenal v Palace (1-1)">
  <actions>
    <action id="11111111-1111-1111-1111-111111111111" actionName=" / Inizio secondo tempo" startTime="00:46:27"/>
    <action id="22222222-2222-2222-2222-222222222222" actionName="(4) M. Salmon / Passaggi" startTime="00:10:00"/>
    <action id="33333333-3333-3333-3333-333333333333" actionName="(8) C. O''Neill / Falli" startTime="00:12:00"/>
    <action id="44444444-4444-4444-4444-444444444444" actionName="(1) J. Porter / Parate" startTime="00:45:35"/>
    <action id="55555555-5555-5555-5555-555555555555" actionName="(1) J. Porter / Tiro fuori dallo specchio" startTime="00:33:39"/>
    <action id="66666666-6666-6666-6666-666666666666" actionName="(7) A. Stevens / Tiri" startTime="00:09:55"/>
    <action id="77777777-7777-7777-7777-777777777777" actionName="(1) J. Porter / Goal subiti" startTime="00:50:27"/>
    <action id="88888888-8888-8888-8888-888888888888" actionName="(11) A. Harriman-Annous / Goal di destro" startTime="01:04:53"/>
    <action id="99999999-9999-9999-9999-999999999999" actionName="(2) T. Julienne / Coinvolgimento nell'azione del goal" startTime="01:04:54"/>
  </actions>
</analysis>
"""


def test_wyscout_analysis_xml_maps_italian_tags_to_one_one() -> None:
    from data_models.events import EventType

    from app.ingest import collect_uploaded_bytes

    rundown = collect_uploaded_bytes(_WYSCOUT_MINI.encode("utf-8"))
    assert rundown.summary.goals == 2
    assert rundown.summary.shots >= 2
    assert rundown.summary.passes >= 1
    names = {profile.player_name: profile for profile in rundown.players}
    assert "C. O'Neill" in names
    assert names["A. Harriman-Annous"].offensive.goals == 1
    assert names["J. Porter"].defensive.goals_against == 1
    assert names["J. Porter"].offensive.total_shots == 0
    assert names["T. Julienne"].offensive.assists == 1
    assert any(name.endswith("Scorer") and "Palace" in name for name in names)
    conceded = next(event for event in rundown.events if event.event_type is EventType.GOAL_CONCEDED)
    assert conceded.period == 2
    assert conceded.minute == 5
    scored = next(
        event
        for event in rundown.events
        if event.is_goal and event.event_type is EventType.GOAL and event.minute == 19
    )
    assert scored.period == 2
    assert scored.second == 53


def test_official_arsenal_palace_wyscout_xml_is_one_one() -> None:
    from pathlib import Path

    fixture = Path(__file__).resolve().parent / "fixtures" / "arsenal_v_palace_1-1.xml"
    rundown = collect_from_film_path(fixture)
    assert rundown.summary.goals == 2
    assert rundown.summary.passes >= 300
    assert rundown.summary.shots >= 10
    names = {profile.player_name: profile for profile in rundown.players}
    assert names["A. Harriman-Annous"].offensive.goals == 1
    assert names["J. Porter"].defensive.goals_against == 1
    assert "C. O'Neill" in names
    assert rundown.summary.player_count >= 15


def test_find_official_tag_xml_pairs_film_with_wyscout_sheet(tmp_path) -> None:
    film = tmp_path / "Arsenal_v_Palace__1-1_.mp4"
    film.write_bytes(b"not-a-real-film")
    sheet = tmp_path / "Arsenal_v_Palace__1-1.xml"
    sheet.write_text(_WYSCOUT_MINI, encoding="utf-8")
    (tmp_path / "Arsenal_v_Palace__1-1_.tags.xml").write_text("<MatchTags/>", encoding="utf-8")
    found = find_official_tag_xml(film, tmp_path)
    assert found == sheet.resolve()


def _palace_analysis_xml(*, actions: int = 40) -> str:
    rows = [
        '    <action id="a0000000-0000-0000-0000-000000000001" '
        'actionName="(10) E. Eze / Goal di sinistro" startTime="00:50:27"/>',
        '    <action id="a0000000-0000-0000-0000-000000000002" '
        'actionName="(10) E. Eze / Tiri" startTime="00:50:20"/>',
        '    <action id="a0000000-0000-0000-0000-000000000003" '
        'actionName="(1) D. Henderson / Parate" startTime="00:20:00"/>',
    ]
    for index in range(actions):
        rows.append(
            f'    <action id="b{index:08d}-0000-0000-0000-000000000000" '
            f'actionName="(10) E. Eze / Passaggi" startTime="00:{index:02d}:10"/>'
        )
    body = "\n".join(rows)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<analysis id="cccccccc-cccc-cccc-cccc-cccccccccccc" '
        'title="Arsenal v Palace (1-1)">\n'
        f"  <actions>\n{body}\n  </actions>\n</analysis>\n"
    )


def test_paired_home_away_analysis_is_official_two_team() -> None:
    from pathlib import Path

    from analytics.match_tags import collect_paired_analysis
    from analytics.team_collect import is_one_sided_sheet, team_profiles_from_rundown
    from app.ingest import collect_official_two_team

    home = (Path(__file__).resolve().parent / "fixtures" / "arsenal_v_palace_1-1.xml").read_text(
        encoding="utf-8"
    )
    away = _palace_analysis_xml()
    rundown = collect_paired_analysis(home, away)
    assert is_one_sided_sheet(rundown) is False
    teams = team_profiles_from_rundown(rundown)
    assert {row.player_name for row in teams} == {"Arsenal", "Palace"}
    by_name = {row.player_name: row for row in teams}
    assert by_name["Arsenal"].offensive.goals == 1
    assert by_name["Palace"].offensive.goals == 1
    assert by_name["Palace"].distribution.passes.total >= 40
    assert by_name["Arsenal"].distribution.passes.total >= 300
    names = {profile.player_name for profile in rundown.players}
    assert "E. Eze" in names
    assert "A. Harriman-Annous" in names
    via_ingest = collect_official_two_team(home.encode("utf-8"), away.encode("utf-8"))
    assert via_ingest.summary.goals == 2
    assert is_one_sided_sheet(via_ingest) is False
