"""One public origin for Streamlit + film upload.

Browsers on the try-link / phone open this portal. Film chunk uploads hit the
same host as the UI (no second flaky API tunnel, no ``Failed to fetch`` to
``127.0.0.1``). FastAPI and Streamlit still run locally; this process proxies.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final
from urllib.parse import urlparse

import httpx
import uvicorn
import websockets
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from api.supervisor import ensure_api_running

LOGGER = logging.getLogger("enjoystats.portal")

DEFAULT_PORTAL_PORT: Final[int] = 8080
DEFAULT_API: Final[str] = "http://127.0.0.1:8000"
DEFAULT_UI: Final[str] = "http://127.0.0.1:8501"

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


def _env_url(name: str, default: str) -> str:
    return (os.environ.get(name) or default).rstrip("/")


def pick_upstream(path: str, *, api: str, ui: str) -> str:
    """Route film/API traffic to FastAPI; everything else to Streamlit."""

    if path == "/upload-film" or path.startswith("/upload-film?"):
        return api
    if path.startswith("/api/"):
        return api
    if path in {"/openapi.json", "/docs", "/redoc", "/health"}:
        return api
    if path.startswith("/docs/") or path.startswith("/redoc/"):
        return api
    return ui


def create_portal(
    *,
    api_origin: str | None = None,
    ui_origin: str | None = None,
) -> Starlette:
    """Build the reverse-proxy Starlette app."""

    api = (api_origin or _env_url("ENJOYSTATS_API_UPSTREAM", DEFAULT_API)).rstrip("/")
    ui = (ui_origin or _env_url("ENJOYSTATS_UI_UPSTREAM", DEFAULT_UI)).rstrip("/")
    client = httpx.AsyncClient(timeout=None, follow_redirects=False)

    async def http_proxy(request: Request) -> Response:
        upstream = pick_upstream(request.url.path, api=api, ui=ui)
        query = request.url.query
        target = f"{upstream}{request.url.path}"
        if query:
            target = f"{target}?{query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        body = await request.body()
        try:
            upstream_response = await client.request(
                request.method,
                target,
                headers=headers,
                content=body,
            )
        except httpx.RequestError as exc:
            LOGGER.warning("Portal upstream error %s %s: %s", request.method, target, exc)
            if upstream == api:
                ensure_api_running(wait_s=8.0)
            return Response(
                content=(
                    b"Upstream service is not reachable. "
                    b"The portal will keep retrying - refresh in a few seconds."
                ),
                status_code=502,
                media_type="text/plain",
            )
        out_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() not in HOP_BY_HOP
            and key.lower() not in {"content-encoding", "content-length"}
        }
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=out_headers,
            media_type=upstream_response.headers.get("content-type"),
        )

    async def ws_proxy(websocket: WebSocket) -> None:
        upstream = pick_upstream(websocket.url.path, api=api, ui=ui)
        parsed = urlparse(upstream)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        target = f"{scheme}://{parsed.netloc}{websocket.url.path}"
        if websocket.url.query:
            target = f"{target}?{websocket.url.query}"

        # Streamlit requires Sec-WebSocket-Protocol to be echoed. Accept only
        # after the upstream handshake so we can pass the negotiated value.
        proto_header = websocket.headers.get("sec-websocket-protocol")
        subprotocols = (
            [part.strip() for part in proto_header.split(",") if part.strip()]
            if proto_header
            else None
        )
        additional_headers: dict[str, str] = {}
        cookie = websocket.headers.get("cookie")
        if cookie:
            additional_headers["Cookie"] = cookie
        origin = websocket.headers.get("origin")
        if origin:
            additional_headers["Origin"] = origin

        try:
            async with websockets.connect(
                target,
                additional_headers=additional_headers or None,
                subprotocols=subprotocols,
                max_size=32 * 1024 * 1024,
                open_timeout=30,
            ) as upstream_ws:
                await websocket.accept(subprotocol=upstream_ws.subprotocol)

                async def client_to_upstream() -> None:
                    try:
                        while True:
                            message = await websocket.receive()
                            if message["type"] == "websocket.disconnect":
                                await upstream_ws.close()
                                return
                            if "text" in message and message["text"] is not None:
                                await upstream_ws.send(message["text"])
                            elif "bytes" in message and message["bytes"] is not None:
                                await upstream_ws.send(message["bytes"])
                    except WebSocketDisconnect:
                        await upstream_ws.close()

                async def upstream_to_client() -> None:
                    try:
                        async for payload in upstream_ws:
                            if isinstance(payload, bytes):
                                await websocket.send_bytes(payload)
                            else:
                                await websocket.send_text(str(payload))
                    except Exception:  # noqa: BLE001 — close both sides on upstream drop
                        try:
                            await websocket.close()
                        except Exception:  # noqa: BLE001
                            pass

                await asyncio.gather(client_to_upstream(), upstream_to_client())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Portal websocket error %s: %s", target, exc)
            try:
                # If accept never happened, this may no-op / error — ignore.
                await websocket.close(code=1011)
            except Exception:  # noqa: BLE001
                pass

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        status = ensure_api_running()
        if status["ok"]:
            LOGGER.info("Portal API ready at %s (%s)", status["url"], status["message"])
        else:
            LOGGER.error("Portal could not start API: %s", status["message"])
        try:
            yield
        finally:
            await client.aclose()

    routes = [
        WebSocketRoute("/{path:path}", ws_proxy),
        Route(
            "/{path:path}",
            http_proxy,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        ),
        Route(
            "/",
            http_proxy,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        ),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


def main(argv: list[str] | None = None) -> None:
    """CLI entry: ensure API, then serve the portal."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="EnjoyStats same-origin portal")
    parser.add_argument("--host", default=os.environ.get("ENJOYSTATS_PORTAL_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ENJOYSTATS_PORTAL_PORT", DEFAULT_PORTAL_PORT)),
    )
    parser.add_argument("--api", default=_env_url("ENJOYSTATS_API_UPSTREAM", DEFAULT_API))
    parser.add_argument("--ui", default=_env_url("ENJOYSTATS_UI_UPSTREAM", DEFAULT_UI))
    args = parser.parse_args(argv)

    status = ensure_api_running()
    if not status["ok"]:
        raise SystemExit(status["message"])

    os.environ["ENJOYSTATS_SAME_ORIGIN_UPLOAD"] = "1"
    app = create_portal(api_origin=args.api, ui_origin=args.ui)
    LOGGER.info(
        "Portal on http://%s:%s  (API %s · UI %s)",
        args.host,
        args.port,
        args.api,
        args.ui,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", ws="websockets")


if __name__ == "__main__":
    main()
