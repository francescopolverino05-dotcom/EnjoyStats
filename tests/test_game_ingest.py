"""Tests for tagged-game upload, auto-collection, and ingest API."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from analytics.game_ingest import (
    collect_game,
    parse_and_collect,
    parse_game_payload,
    rundown_from_mapping,
    rundown_to_json,
)
from analytics.sample_game import PLAYMAKER_ID, STRIKER_ID, sample_game_payload
from api.main import create_app
from app.ingest import actions_from_events, collect_sample_match, load_from_rundown
from data_models.events import EventType
from tests.fakes import InMemoryProfileStore


def test_parse_event_array_and_collect_profiles() -> None:
    payload = sample_game_payload()
    raw = [event.model_dump(mode="json") for event in payload.events]
    parsed = parse_game_payload(raw)
    rundown = collect_game(parsed)
    assert rundown.summary.event_count == len(payload.events)
    assert rundown.summary.player_count == 3
    assert rundown.summary.goals == 1
    assert rundown.summary.shots >= 1
    names = {profile.player_id: profile for profile in rundown.players}
    playmaker = names[PLAYMAKER_ID]
    striker = names[STRIKER_ID]
    assert playmaker.distribution.passes.total >= 3
    assert playmaker.offensive.assists == 1
    assert striker.offensive.goals == 1
    assert striker.offensive.shots_on_target >= 1
    assert striker.defensive.ground_duels.success == 1


def test_sample_match_rundown_covers_four_pillars() -> None:
    rundown = collect_sample_match()
    assert rundown.summary.duration_minutes > 40
    by_id = {profile.player_id: profile for profile in rundown.players}
    playmaker = by_id[PLAYMAKER_ID]
    striker = by_id[STRIKER_ID]
    assert playmaker.player_name == "Alex Playmaker"
    assert playmaker.offensive.throw_ins == 1
    assert playmaker.distribution.crosses.total == 1
    assert playmaker.possession.percentage > 0
    assert striker.offensive.offsides == 1
    assert striker.offensive.blocked_shots == 1
    defender = next(profile for profile in rundown.players if profile.position == "CB")
    assert defender.defensive.yellow_cards == 1
    assert defender.defensive.blocks.shots == 1
    assert defender.defensive.interceptions.total == 1
    load = load_from_rundown(rundown, STRIKER_ID)
    assert load.source == "collected"
    assert load.profile.offensive.goals == 1
    assert any(action.is_goal for action in load.actions)
    assert actions_from_events(rundown.events, PLAYMAKER_ID)
    restored = rundown_from_mapping(rundown_to_json(rundown))
    assert restored.summary.goals == rundown.summary.goals
    assert restored.players[0].player_id == rundown.players[0].player_id


def test_parse_rejects_empty_and_mixed_matches() -> None:
    try:
        parse_game_payload([])
        assert False, "expected empty list to fail"
    except ValueError:
        pass
    payload = sample_game_payload()
    events = [event.model_dump(mode="json") for event in payload.events]
    events[0]["match_id"] = str(uuid4())
    try:
        parse_and_collect({"events": events})
        assert False, "expected mixed match_id to fail"
    except ValueError as exc:
        assert "single match_id" in str(exc)


def test_ingest_api_collects_and_persists() -> None:
    store = InMemoryProfileStore()
    application = create_app(aggregator=store)
    payload = sample_game_payload()
    body = payload.model_dump(mode="json")
    with TestClient(application) as client:
        response = client.post(f"/api/v1/matches/{payload.match_id}/ingest", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["upserted_events"] == len(payload.events)
    assert data["upserted_profiles"] == 3
    assert data["summary"]["goals"] == 1
    striker = next(row for row in data["players"] if row["player_id"] == str(STRIKER_ID))
    assert striker["offensive"]["goals"] == 1
    assert (STRIKER_ID, payload.match_id) in store.rows
    assert len(store.events) == len(payload.events)


def test_ingest_api_rejects_mismatched_match_id() -> None:
    store = InMemoryProfileStore()
    application = create_app(aggregator=store)
    payload = sample_game_payload()
    body = payload.model_dump(mode="json")
    with TestClient(application) as client:
        response = client.post(f"/api/v1/matches/{uuid4()}/ingest", json=body)
    assert response.status_code == 400
    assert "match_id" in response.json()["message"]


def test_film_stream_upload_writes_inbox(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(inbox))
    store = InMemoryProfileStore()
    application = create_app(aggregator=store)
    payload = b"fake-match-bytes"
    with TestClient(application) as client:
        page = client.get("/upload-film")
        assert page.status_code == 200
        assert "Save film to inbox" in page.text
        response = client.post(
            "/api/v1/matches/film?filename=derby.mp4",
            content=payload,
            headers={"Content-Type": "application/octet-stream", "X-Filename": "derby.mp4"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filename"] == "derby.mp4"
    assert body["size_bytes"] == len(payload)
    saved = Path(body["path"])
    assert saved.read_bytes() == payload


def test_film_stream_upload_rejects_multipart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(tmp_path / "inbox"))
    application = create_app(aggregator=InMemoryProfileStore())
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/matches/film",
            files={"film": ("match.mp4", b"abc", "video/mp4")},
        )
    assert response.status_code == 415


def test_bundled_sample_game_file_collects() -> None:
    from pathlib import Path

    sample = Path(__file__).resolve().parents[1] / "data" / "sample_game.json"
    rundown = parse_and_collect(json.loads(sample.read_text(encoding="utf-8")))
    assert rundown.summary.player_count == 3
    assert rundown.summary.goals == 1
    payload = sample_game_payload()
    dumped = json.dumps(payload.model_dump(mode="json"))
    rundown = parse_and_collect(json.loads(dumped))
    assert rundown.match_id == payload.match_id
    assert {event.event_type for event in rundown.events} >= {
        EventType.PASS,
        EventType.SHOT,
        EventType.INTERCEPTION,
    }
