"""Stream a match film to the inbox without going through Streamlit's uploader.

Streamlit 1.64 stores browser picks via ``PUT /_stcore/upload_file``. Large
match films disconnect mid-body (``ClientDisconnect``). This router accepts
an ``application/octet-stream`` body and writes 8 MiB chunks to
``.local-run/inbox`` so the dashboard can collect from disk.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import Field

from analytics.video_auto_collect import (
    MAX_VIDEO_BYTES,
    VIDEO_SUFFIXES,
    film_inbox_dir,
    safe_film_name,
)
from data_models.player_stats import StrictModel

film_router = APIRouter(tags=["film-upload"])

_UPLOAD_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EnjoyStats · Upload match film</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, sans-serif; background:#0f172a;
           color:#e2e8f0; margin:0; padding:1.1rem; }
    .card { max-width: 640px; margin: 0 auto; background:#111827;
            border:1px solid #1e293b; border-radius:16px; padding:1.15rem 1.3rem; }
    h1 { font-size:1.2rem; margin:0 0 .35rem; }
    p { color:#94a3b8; font-size:.92rem; line-height:1.45; }
    input[type=file] { width:100%; margin:.75rem 0; color:#e2e8f0; }
    button { background:#16a34a; color:#fff; border:0; border-radius:10px;
             padding:.65rem 1rem; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.5; cursor:not-allowed; }
    #bar { height:10px; background:#1e293b; border-radius:999px; overflow:hidden;
           margin-top:1rem; }
    #bar > i { display:block; height:100%; width:0; background:#22c55e; }
    #msg { margin-top:.7rem; font-size:.9rem; min-height:1.2em; }
    .ok { color:#86efac; }
    .err { color:#fca5a5; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Upload a match film</h1>
    <p>Streams the file straight to disk (up to 3&nbsp;GB). This skips
    Streamlit’s browser picker, which drops large transfers mid-upload.</p>
    <input id="file" type="file" accept="video/*,.mp4,.mov,.mkv,.avi,.m4v,.webm">
    <button id="go" type="button">Save film to inbox</button>
    <div id="bar"><i id="fill"></i></div>
    <div id="msg"></div>
  </div>
  <script>
  const fileInput = document.getElementById("file");
  const go = document.getElementById("go");
  const fill = document.getElementById("fill");
  const msg = document.getElementById("msg");
  function fmt(n) {
    if (n >= 1073741824) return (n / 1073741824).toFixed(2) + " GB";
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(0) + " KB";
    return n + " B";
  }
  go.onclick = () => {
    const file = fileInput.files[0];
    if (!file) { msg.className = "err"; msg.textContent = "Choose a match film first."; return; }
    if (file.size > 3 * 1024 * 1024 * 1024) {
      msg.className = "err"; msg.textContent = "Film exceeds the 3 GB limit."; return;
    }
    go.disabled = true;
    msg.className = "";
    msg.textContent = "Starting upload…";
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/matches/film?filename=" + encodeURIComponent(file.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Filename", file.name);
    xhr.upload.onprogress = (ev) => {
      const pct = ev.lengthComputable && ev.total ? ev.loaded / ev.total : 0;
      fill.style.width = (pct * 100).toFixed(1) + "%";
      msg.textContent = "Uploading " + fmt(ev.loaded)
        + (ev.lengthComputable ? " / " + fmt(ev.total) : "");
    };
    xhr.onload = () => {
      go.disabled = false;
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (err) { body = {}; }
      if (xhr.status >= 200 && xhr.status < 300) {
        fill.style.width = "100%";
        msg.className = "ok";
        msg.textContent = "Saved " + (body.filename || file.name)
          + ". Return to the dashboard, pick it under Films on this machine, "
          + "and click Collect stats from film.";
      } else {
        msg.className = "err";
        msg.textContent = body.message || body.detail
          || ("Upload failed (HTTP " + xhr.status + ").");
      }
    };
    xhr.onerror = () => {
      go.disabled = false;
      msg.className = "err";
      msg.textContent = "The connection dropped while uploading. Copy the film "
        + "into .local-run/inbox on this machine instead.";
    };
    xhr.send(file);
  };
  </script>
</body>
</html>
"""


class FilmUploadResult(StrictModel):
    """Path of a film that finished streaming to the inbox."""

    filename: str
    path: str
    size_bytes: int = Field(ge=1)
    inbox: str


def _destination_for(filename: str) -> Path:
    inbox = film_inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    safe = safe_film_name(filename)
    if Path(safe).suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a match film (mp4, mov, mkv, avi, m4v, webm).",
        )
    return inbox / safe


@film_router.get("/upload-film", response_class=HTMLResponse, include_in_schema=False)
async def upload_film_page() -> HTMLResponse:
    """Browser form that streams a film to :func:`upload_match_film`."""

    return HTMLResponse(_UPLOAD_PAGE)


@film_router.post(
    "/api/v1/matches/film",
    response_model=FilmUploadResult,
    summary="Stream a match film into the local inbox",
)
async def upload_match_film(
    request: Request,
    filename: str = Query(default="match.mp4"),
    x_filename: str | None = Header(default=None, alias="X-Filename"),
) -> FilmUploadResult:
    """Write the raw request body to ``.local-run/inbox`` in 8 MiB chunks."""

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type.startswith("multipart/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Send the film as application/octet-stream, not multipart.",
        )
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header.",
            ) from exc
        if declared > MAX_VIDEO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Match film exceeds the 3 GB upload limit.",
            )

    chosen = x_filename or filename or "match.mp4"
    destination = _destination_for(chosen)
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with destination.open("wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_VIDEO_BYTES:
                    out.close()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Match film exceeds the 3 GB upload limit.",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not save the match film ({exc}).",
        ) from exc
    if written <= 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded film is empty.",
        )
    return FilmUploadResult(
        filename=destination.name,
        path=str(destination),
        size_bytes=written,
        inbox=str(destination.parent),
    )
