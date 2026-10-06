#!/usr/bin/env python3
"""Minimal Railway Web probe — proves the public domain target port is correct."""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        port = os.environ.get("PORT", "?")
        body = (
            "<!doctype html><html><body style='font-family:sans-serif;padding:2rem'>"
            "<h1>StatMan Web — port OK</h1>"
            f"<p>This process is listening on <strong>PORT={port}</strong>.</p>"
            "<p>If you see this page, the Railway domain target port matches. "
            "Tell the agent to switch back to Streamlit.</p>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"statman-web-diag: {fmt % args}", flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8501"))
    print(f"statman-web-diag: listening on 0.0.0.0:{port}", flush=True)
    print(
        f"statman-web-diag: Networking domain target port MUST be {port}",
        flush=True,
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
