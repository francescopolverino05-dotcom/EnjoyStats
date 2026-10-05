"""Tests for the StatMan (Grok Bot) bridge."""

from __future__ import annotations

from analytics.statman import (
    DEFAULT_STATMAN_BOT_URL,
    statman_bot_url,
    trigger_statman_analyse,
    webhook_configured,
)


def test_statman_bot_url_defaults_to_francescos_bot(monkeypatch) -> None:
    monkeypatch.delenv("STATMAN_BOT_URL", raising=False)
    assert statman_bot_url() == DEFAULT_STATMAN_BOT_URL
    assert "Nzfsi4AUBHfUB0tsb0aCx" in statman_bot_url()


def test_statman_bot_url_override(monkeypatch) -> None:
    monkeypatch.setenv("STATMAN_BOT_URL", "https://x.ai/bot/custom")
    assert statman_bot_url() == "https://x.ai/bot/custom"


def test_webhook_configured_requires_url_and_key(monkeypatch) -> None:
    monkeypatch.delenv("STATMAN_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("STATMAN_WEBHOOK_KEY", raising=False)
    assert webhook_configured() is False
    assert webhook_configured(url="https://hooks.example/x", key="") is False
    assert webhook_configured(url="https://hooks.example/x", key="secret") is True


def test_trigger_statman_analyse_posts_bearer(monkeypatch) -> None:
    calls: list[object] = []

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b'{"started":true}'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _fake_urlopen(request: object, timeout: float = 0) -> _Resp:
        calls.append((request, timeout))
        return _Resp()

    monkeypatch.setattr("analytics.statman.urlopen", _fake_urlopen)
    result = trigger_statman_analyse(
        webhook_url="https://hooks.example/statman",
        webhook_key="sekret",
        film="https://example.com/pisa.mp4",
        home_team="Pisa",
        away_team="Perugia",
    )
    assert result["ok"] is True
    assert "StatMan run started" in result["message"]
    assert len(calls) == 1
    request, _timeout = calls[0]
    assert request.full_url == "https://hooks.example/statman"
    assert request.get_header("Authorization") == "Bearer sekret"
    body = request.data.decode("utf-8")
    assert "Pisa" in body and "Perugia" in body
    assert "pisa.mp4" in body


def test_trigger_without_webhook_raises() -> None:
    try:
        trigger_statman_analyse(webhook_url="", webhook_key="")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "webhook is not configured" in str(exc)
