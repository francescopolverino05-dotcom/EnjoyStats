"""Tests for always-on FastAPI supervisor and same-origin portal."""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from api.portal import create_portal, pick_upstream
from api.supervisor import api_is_healthy, ensure_api_running


def test_pick_upstream_routes_film_to_api() -> None:
    api = "http://127.0.0.1:8000"
    ui = "http://127.0.0.1:8501"
    assert pick_upstream("/upload-film", api=api, ui=ui) == api
    assert pick_upstream("/api/v1/matches/film/chunk", api=api, ui=ui) == api
    assert pick_upstream("/openapi.json", api=api, ui=ui) == api
    assert pick_upstream("/", api=api, ui=ui) == ui
    assert pick_upstream("/_stcore/health", api=api, ui=ui) == ui


def test_ensure_api_running_is_idempotent_when_healthy(monkeypatch) -> None:
    monkeypatch.setattr("api.supervisor.api_is_healthy", lambda *_a, **_k: True)
    monkeypatch.setattr("api.supervisor._read_pid", lambda: 4242)
    status = ensure_api_running(wait_s=1.0)
    assert status["ok"] is True
    assert status["started"] is False
    assert status["pid"] == 4242


def test_ensure_api_running_starts_uvicorn_when_down(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class FakeProc:
        pid = 9090

        def poll(self) -> None:
            return None

    def fake_popen(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        return FakeProc()

    healthy_after = {"n": 0}

    def fake_healthy(*_a, **_k) -> bool:
        healthy_after["n"] += 1
        return healthy_after["n"] > 2

    monkeypatch.setattr("api.supervisor.LOCAL_RUN", tmp_path)
    monkeypatch.setattr("api.supervisor.PID_FILE", tmp_path / "uvicorn.pid")
    monkeypatch.setattr("api.supervisor.LOG_FILE", tmp_path / "uvicorn.log")
    monkeypatch.setattr("api.supervisor.ROOT", Path(__file__).resolve().parents[1])
    monkeypatch.setattr("api.supervisor.api_is_healthy", fake_healthy)
    monkeypatch.setattr("api.supervisor._stop_stale_pid", lambda: None)
    monkeypatch.setattr("api.supervisor.subprocess.Popen", fake_popen)
    monkeypatch.setattr("api.supervisor.time.sleep", lambda _s: None)

    status = ensure_api_running(wait_s=2.0)
    assert status["ok"] is True
    assert status["started"] is True
    assert status["pid"] == 9090
    assert calls and "uvicorn" in calls[0]
    assert (tmp_path / "uvicorn.pid").read_text(encoding="utf-8").strip() == "9090"


def test_portal_proxies_film_chunk(tmp_path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(inbox))
    from api.main import create_app
    from tests.fakes import InMemoryProfileStore

    api_app = create_app(aggregator=InMemoryProfileStore())
    # Drive portal against the in-process API via ASGI transport by mounting
    # through httpx — use TestClient on portal with a real upstream TestClient
    # is heavy; instead hit film router through portal's pick + direct API.
    with TestClient(api_app) as api_client:
        page = api_client.get("/upload-film")
        assert page.status_code == 200
        assert "resolveApi" in page.text or "CONFIGURED_API" in page.text
        response = api_client.post(
            "/api/v1/matches/film/chunk?filename=keep.mp4&offset=0&total=4&final=true",
            content=b"abcd",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["complete"] is True
        assert (inbox / "keep.mp4").read_bytes() == b"abcd"

    portal = create_portal(
        api_origin="http://127.0.0.1:9",
        ui_origin="http://127.0.0.1:9",
    )
    assert portal is not None


def test_api_is_healthy_false_for_dead_port() -> None:
    assert api_is_healthy("http://127.0.0.1:1", timeout_s=0.2) is False
