"""Tests for line-ups, jersey OCR helpers, and game-week batch queue."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from analytics.batch_collect import list_inbox_films, start_batch_collect, summarize_batch
from analytics.jersey_ocr import ocr_available
from analytics.lineups import example_lineup_csv, parse_lineup_bytes, parse_lineup_csv
from analytics.video_auto_collect import (
    Track,
    _assign_lineup_roster,
    collect_from_video,
    write_synthetic_match_clip,
)
from analytics.lineups import MatchLineups, LineupPlayer


def test_parse_lineup_csv_sample() -> None:
    lineups = parse_lineup_csv(example_lineup_csv(), home_team="Pisa", away_team="Perugia")
    assert lineups.home_team == "Pisa"
    assert len(lineups.home) == 3
    assert lineups.by_jersey("home", 9).name == "Rossi"
    assert lineups.by_jersey("away", 10).name == "Bianchi"


def test_parse_lineup_json_bytes() -> None:
    raw = (
        b'{"home_team":"Pisa","away_team":"Perugia",'
        b'"home":[{"jersey":9,"name":"Rossi","position":"ST"}],'
        b'"away":[{"jersey":10,"name":"Bianchi","position":"ST"}]}'
    )
    lineups = parse_lineup_bytes(raw, filename="sheet.json")
    assert lineups.home[0].name == "Rossi"


def test_parse_lineup_json_accepts_spreadsheet_and_alias_shapes() -> None:
    from analytics.lineups import parse_lineup_json

    # Pandas / Excel often emit jersey as 1.0
    float_sheet = {
        "home_team": "Napoli",
        "away_team": "Lazio",
        "home_players": [{"jersey": 1.0, "name": "Mattia Magliano"}],
        "away_players": [{"jersey": 9.0, "playerName": "Carmine Mennea"}],
    }
    parsed = parse_lineup_json(float_sheet)
    assert parsed.home[0].jersey == 1
    assert parsed.away[0].name == "Carmine Mennea"

    first_last = {
        "home": [{"number": 7, "firstName": "Luigi", "lastName": "Riccio"}],
        "away": [{"shirtNumber": 10, "first": "Daniele", "last": "Lulaj"}],
    }
    parsed = parse_lineup_json(first_last)
    assert parsed.home[0].name == "Luigi Riccio"
    assert parsed.away[0].jersey == 10

    mixed_rows = [
        {"side": "home", "jersey": "07", "name": "Verdi"},
        {"side": "away", "numero": 11, "nome": "Bianchi"},
    ]
    parsed = parse_lineup_json(mixed_rows)
    assert len(parsed.home) == 1 and parsed.home[0].jersey == 7
    assert parsed.away[0].name == "Bianchi"

    nested = {
        "lineups": {
            "home_team": "Pisa",
            "away_team": "Perugia",
            "home": [{"jersey": 9, "name": "Rossi"}],
            "away": [{"jersey": 10, "name": "Bianchi"}],
        }
    }
    parsed = parse_lineup_json(nested)
    assert parsed.home_team == "Pisa"
    assert parsed.by_jersey("away", 10).name == "Bianchi"

    teams = {
        "teams": [
            {"name": "Napoli", "players": [{"jersey": 1, "name": "Magliano"}]},
            {"name": "Lazio", "players": [{"jersey": 1, "name": "Bekirov"}]},
        ]
    }
    parsed = parse_lineup_json(teams)
    assert parsed.home_team == "Napoli"
    assert parsed.away[0].name == "Bekirov"

    double = (
        '"{\\"home\\":[{\\"jersey\\":1,\\"name\\":\\"A\\"}],'
        '\\"away\\":[{\\"jersey\\":2,\\"name\\":\\"B\\"}]}"'
    )
    parsed = parse_lineup_json(double)
    assert len(parsed.home) + len(parsed.away) == 2


def test_parse_lineup_semicolon_csv() -> None:
    raw = "side;jersey;name\nhome;1;Rossi\naway;10;Bianchi\n"
    lineups = parse_lineup_bytes(
        raw.encode(),
        filename="sheet.csv",
        home_team="Pisa",
        away_team="Perugia",
    )
    assert lineups.home[0].name == "Rossi"
    assert lineups.away_team == "Perugia"


def test_assign_lineup_roster_uses_real_names() -> None:
    lineups = MatchLineups(
        home_team="Pisa",
        away_team="Perugia",
        home=(LineupPlayer("home", 9, "Rossi", "ST"),),
        away=(LineupPlayer("away", 10, "Bianchi", "ST"),),
    )
    players = [
        Track(track_id=1, kind="player", xs=[80.0], ys=[50.0], frames=[0], team=0, jersey=9),
        Track(track_id=2, kind="player", xs=[20.0], ys=[50.0], frames=[0], team=1, jersey=10),
    ]
    match_id = uuid4()
    team_ids = (uuid4(), uuid4())
    roster, ids = _assign_lineup_roster(
        players,
        match_id=match_id,
        team_ids=team_ids,
        home_name="Pisa",
        away_name="Perugia",
        goal_x=(100.0, 0.0),
        lineups=lineups,
    )
    names = {row.player_name for row in roster}
    assert "Rossi" in names
    assert "Bianchi" in names
    assert ids[1] != ids[2]


def test_ocr_available_flag() -> None:
    assert isinstance(ocr_available(), bool)


def test_film_collect_with_lineups(tmp_path: Path) -> None:
    clip = write_synthetic_match_clip(tmp_path / "gw1.avi")
    lineups = parse_lineup_csv(example_lineup_csv(), home_team="Pisa", away_team="Perugia")
    rundown = collect_from_video(
        clip,
        sample_hz=8.0,
        max_sample_frames=24,
        home_kit_hex="#1450B4",
        away_kit_hex="#C82814",
        home_team_name="Pisa",
        away_team_name="Perugia",
        lineups=lineups,
    )
    assert rundown.summary.home_team_name == "Pisa"
    assert rundown.events


def test_batch_collect_queues_inbox(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    jobs = tmp_path / "jobs"
    inbox.mkdir()
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(inbox))
    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(jobs))
    from analytics import video_auto_collect as vac

    monkeypatch.setattr(vac, "MIN_READY_VIDEO_BYTES", 1)
    monkeypatch.setattr(vac, "film_is_seekable", lambda _p: True)
    write_synthetic_match_clip(inbox / "gw1_pisa_perugia.avi")
    write_synthetic_match_clip(inbox / "gw1_other.avi")
    films = list_inbox_films()
    assert len(films) == 2
    # Don't spawn real subprocesses — patch start_collect_job.
    from analytics import batch_collect as bc

    created: list[Path] = []

    def _fake_start(film, **kwargs):
        jobs.mkdir(parents=True, exist_ok=True)
        path = jobs / f"{film.stem}.status.json"
        path.write_text('{"state":"queued","film":"%s"}' % film, encoding="utf-8")
        created.append(path)
        return path

    monkeypatch.setattr(bc, "start_collect_job", _fake_start)
    batch_path = start_batch_collect(films, home_team_name="Pisa", away_team_name="Perugia")
    assert batch_path.is_file()
    assert len(created) == 2
    summary = summarize_batch(bc.read_batch_status(batch_path) or {})
    assert summary["total"] == 2
