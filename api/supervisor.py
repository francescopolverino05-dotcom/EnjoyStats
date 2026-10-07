"""Keep the FastAPI film-upload process running.

The Streamlit dashboard and the one-port portal both call
:func:`ensure_api_running` so uploads never depend on someone remembering
to start Uvicorn by hand.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCAL_RUN = ROOT / ".local-run"
PID_FILE = LOCAL_RUN / "uvicorn.pid"
LOG_FILE = LOCAL_RUN / "uvicorn.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_UVICORN_APP = "api.main:app"
UPLOAD_ONLY_UVICORN_APP = "api.upload_app:app"


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def uvicorn_app_target() -> str:
    """ASGI target for the local film-upload Uvicorn process."""

    override = os.environ.get("ENJOYSTATS_UVICORN_APP", "").strip()
    if override:
        return override
    if _truthy("STATMAN_UPLOAD_ONLY") or _truthy("STATMAN_USE_EXTERNAL_WORKER"):
        return UPLOAD_ONLY_UVICORN_APP
    return DEFAULT_UVICORN_APP


def api_base_url(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    """Return the local FastAPI origin."""

    return f"http://{host}:{port}"


def api_is_healthy(
    base_url: str | None = None,
    *,
    timeout_s: float = 1.5,
) -> bool:
    """Return whether FastAPI answers OpenAPI on the given origin."""

    origin = (base_url or api_base_url()).rstrip("/")
    url = f"{origin}/openapi.json"
    try:
        with urlopen(url, timeout=timeout_s) as response:  # noqa: S310 — local health check
            return 200 <= getattr(response, "status", 200) < 300
    except (URLError, OSError, TimeoutError, ValueError):
        return False


def _read_pid() -> int | None:
    if not PID_FILE.is_file():
        return None
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _write_pid(pid: int) -> None:
    LOCAL_RUN.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{pid}\n", encoding="utf-8")


def _stop_stale_pid() -> None:
    pid = _read_pid()
    if pid is None:
        return
    if _pid_alive(pid) and api_is_healthy():
        return
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.1)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def ensure_api_running(
    *,
    host: str | None = None,
    port: int | None = None,
    wait_s: float = 25.0,
) -> dict[str, Any]:
    """Start Uvicorn when the film-upload API is down.

    Returns a status dict: ``ok``, ``started``, ``pid``, ``url``, ``message``.
    Safe to call from Streamlit on every page load — cheap when already healthy.
    """

    bind_host = (host or os.environ.get("ENJOYSTATS_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST
    # Health checks always hit loopback even when uvicorn binds 0.0.0.0.
    check_host = "127.0.0.1"
    bind_port = int(port or os.environ.get("ENJOYSTATS_PORT") or DEFAULT_PORT)
    url = api_base_url(host=check_host, port=bind_port)

    if api_is_healthy(url):
        pid = _read_pid()
        return {
            "ok": True,
            "started": False,
            "pid": pid,
            "url": url,
            "message": "Film upload API is running.",
        }

    _stop_stale_pid()
    LOCAL_RUN.mkdir(parents=True, exist_ok=True)
    (LOCAL_RUN / "inbox").mkdir(parents=True, exist_ok=True)
    (LOCAL_RUN / "uploads").mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["PYTHONPATH"] = (
        f"{ROOT}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(ROOT)
    )
    log_handle = LOG_FILE.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(  # noqa: S603 — controlled local launcher
            [
                os.environ.get("PYTHON", "python3"),
                "-m",
                "uvicorn",
                uvicorn_app_target(),
                "--host",
                bind_host if bind_host != "127.0.0.1" else "0.0.0.0",
                "--port",
                str(bind_port),
                "--log-level",
                "info",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()

    _write_pid(proc.pid)
    deadline = time.monotonic() + max(wait_s, 1.0)
    while time.monotonic() < deadline:
        if api_is_healthy(url):
            return {
                "ok": True,
                "started": True,
                "pid": proc.pid,
                "url": url,
                "message": "Started film upload API.",
            }
        if proc.poll() is not None:
            break
        time.sleep(0.25)

    return {
        "ok": False,
        "started": True,
        "pid": proc.pid,
        "url": url,
        "message": (f"Film upload API did not become ready on {url}. " f"See {LOG_FILE}."),
    }
