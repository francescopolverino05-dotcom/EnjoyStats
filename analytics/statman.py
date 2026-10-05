"""StatMan (Grok Bot) bridge — preferred high-accuracy tag source for EnjoyStats.

StatMan lives at xAI Grok Bot and tags matches from video, then delivers
OnceSport XMLs (plus HTML/CSV). Grok Bots are not a normal request/response
API: EnjoyStats can (1) open / link the Bot, (2) optionally POST a routine
webhook to start a run, and (3) collect the OnceSport XML StatMan returns.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_STATMAN_BOT_URL = "https://x.ai/bot/Nzfsi4AUBHfUB0tsb0aCx"


def statman_bot_url() -> str:
    """Public StatMan Grok Bot share link (override with STATMAN_BOT_URL)."""

    return os.environ.get("STATMAN_BOT_URL", "").strip() or DEFAULT_STATMAN_BOT_URL


def statman_webhook_url() -> str:
    """Optional routine webhook URL from StatMan (STATMAN_WEBHOOK_URL)."""

    return os.environ.get("STATMAN_WEBHOOK_URL", "").strip()


def statman_webhook_key() -> str:
    """Bearer key for the StatMan routine webhook (STATMAN_WEBHOOK_KEY)."""

    return os.environ.get("STATMAN_WEBHOOK_KEY", "").strip()


def webhook_configured(*, url: str | None = None, key: str | None = None) -> bool:
    """Whether EnjoyStats can start a StatMan run via webhook."""

    return bool(
        (url if url is not None else statman_webhook_url())
        and (key if key is not None else statman_webhook_key())
    )


def trigger_statman_analyse(
    *,
    webhook_url: str | None = None,
    webhook_key: str | None = None,
    film: str = "",
    home_team: str = "",
    away_team: str = "",
    note: str = "",
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """POST a StatMan routine webhook to start a tagging run.

    A ``200`` means Grok Bot accepted the call and started work — not that
    the XML is ready. Collect the OnceSport XML from StatMan's chat when done.
    """

    url = (webhook_url if webhook_url is not None else statman_webhook_url()).strip()
    key = (webhook_key if webhook_key is not None else statman_webhook_key()).strip()
    if not url or not key:
        raise ValueError(
            "StatMan webhook is not configured. In Grok Bot, ask StatMan to add a "
            "webhook routine, then set STATMAN_WEBHOOK_URL and STATMAN_WEBHOOK_KEY "
            "(or paste them in the StatMan panel)."
        )
    payload = {
        "source": "enjoystats",
        "action": "analyse_match",
        "film": film,
        "home_team": home_team,
        "away_team": away_team,
        "note": note
        or (
            "Tag this match at OnceSport density. Return Home and Away "
            "OnceSport analysis XMLs (and CSV/HTML if available) for EnjoyStats."
        ),
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(
            request, timeout=timeout_s
        ) as response:  # noqa: S310 — user-configured webhook
            status = int(getattr(response, "status", 200) or 200)
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise ValueError(f"StatMan webhook rejected the call (HTTP {exc.code}): {detail}") from exc
    except URLError as exc:
        raise ValueError(f"StatMan webhook unreachable ({exc.reason}).") from exc
    if status != 200:
        raise ValueError(f"StatMan webhook returned HTTP {status}: {body[:400]}")
    return {
        "ok": True,
        "status": status,
        "message": (
            "StatMan run started. When it finishes, download the OnceSport "
            "XML(s) from the StatMan chat and Collect them below."
        ),
        "body": body[:1000],
    }
