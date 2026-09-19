"""Stream a match film to the inbox without going through Streamlit's uploader.

Streamlit 1.64 stores browser picks via ``PUT /_stcore/upload_file``. Large
match films disconnect mid-body (``ClientDisconnect``). This router accepts
an ``application/octet-stream`` body — whole file or 4 MiB chunks — and
writes them to ``.local-run/inbox`` so the dashboard can collect from disk.
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


def upload_page_html(api_origin: str = "") -> str:
    """Browser form that uploads a film in 4 MiB chunks.

    ``api_origin`` is the FastAPI origin (no trailing slash). Empty means
    same-origin, used when this page is served from FastAPI itself.
    """

    origin = api_origin.rstrip("/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EnjoyStats · Upload match film</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; background:#0f172a;
           color:#e2e8f0; margin:0; padding:1.1rem; }}
    .card {{ max-width: 640px; margin: 0 auto; background:#111827;
            border:1px solid #1e293b; border-radius:16px; padding:1.15rem 1.3rem; }}
    h1 {{ font-size:1.2rem; margin:0 0 .35rem; }}
    p {{ color:#94a3b8; font-size:.92rem; line-height:1.45; }}
    input[type=file] {{ width:100%; margin:.75rem 0; color:#e2e8f0; }}
    button {{ background:#16a34a; color:#fff; border:0; border-radius:10px;
             padding:.65rem 1rem; font-weight:700; cursor:pointer; }}
    button:disabled {{ opacity:.5; cursor:not-allowed; }}
    #bar {{ height:10px; background:#1e293b; border-radius:999px; overflow:hidden;
           margin-top:1rem; }}
    #bar > i {{ display:block; height:100%; width:0; background:#22c55e; }}
    #msg {{ margin-top:.7rem; font-size:.9rem; min-height:1.2em; }}
    .ok {{ color:#86efac; }}
    .err {{ color:#fca5a5; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Upload a match film</h1>
    <p>Sends the file in 4&nbsp;MB chunks (up to 3&nbsp;GB). After it says
    Saved, pick the film under <b>Films on this machine</b> and click
    Collect stats from film.</p>
    <input id="file" type="file" accept="video/*,.mp4,.mov,.mkv,.avi,.m4v,.webm">
    <button id="go" type="button">Save film to inbox</button>
    <div id="bar"><i id="fill"></i></div>
    <div id="msg"></div>
  </div>
  <script>
  const API = {origin!r};
  const CHUNK = 4 * 1024 * 1024;
  const fileInput = document.getElementById("file");
  const go = document.getElementById("go");
  const fill = document.getElementById("fill");
  const msg = document.getElementById("msg");
  function fmt(n) {{
    if (n >= 1073741824) return (n / 1073741824).toFixed(2) + " GB";
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(0) + " KB";
    return n + " B";
  }}
  function fail(text) {{
    go.disabled = false;
    msg.className = "err";
    msg.textContent = text;
  }}
  go.onclick = async () => {{
    const file = fileInput.files[0];
    if (!file) {{ fail("Choose a match film first."); return; }}
    if (file.size > 3 * 1024 * 1024 * 1024) {{
      fail("Film exceeds the 3 GB limit."); return;
    }}
    go.disabled = true;
    msg.className = "";
    fill.style.width = "2%";
    msg.textContent = "Preparing " + fmt(file.size) + "…";
    let offset = 0;
    let savedName = file.name;
    try {{
      while (offset < file.size) {{
        const end = Math.min(offset + CHUNK, file.size);
        const blob = file.slice(offset, end);
        msg.textContent = "Uploading " + fmt(offset) + " / " + fmt(file.size);
        fill.style.width = Math.max(2, (100 * offset / file.size)).toFixed(1) + "%";
        const url = API + "/api/v1/matches/film/chunk?filename="
          + encodeURIComponent(file.name)
          + "&offset=" + offset
          + "&total=" + file.size
          + "&final=" + (end >= file.size ? "true" : "false");
        const res = await fetch(url, {{
          method: "POST",
          headers: {{
            "Content-Type": "application/octet-stream",
            "X-Filename": file.name
          }},
          body: blob
        }});
        let body = {{}};
        try {{ body = await res.json(); }} catch (err) {{ body = {{}}; }}
        if (!res.ok) {{
          fail(body.message || body.detail || ("Upload failed (HTTP " + res.status + ")."));
          return;
        }}
        if (body.filename) savedName = body.filename;
        offset = end;
        fill.style.width = (100 * offset / file.size).toFixed(1) + "%";
      }}
      msg.className = "ok";
      msg.textContent = "Saved " + savedName
        + ". In the sidebar, pick it under Films on this machine, "
        + "then click Collect stats from film.";
      go.disabled = false;
    }} catch (err) {{
      fail("The connection dropped while uploading. Copy the film "
        + "into .local-run/inbox on this machine instead.");
    }}
  }};
  </script>
</body>
</html>
"""


class FilmUploadResult(StrictModel):
    """Path of a film that finished streaming to the inbox."""

    filename: str
    path: str
    size_bytes: int = Field(ge=0)
    inbox: str
    complete: bool = True


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


async def _read_limited_body(request: Request, *, already: int = 0) -> bytes:
    chunks: list[bytes] = []
    written = already
    async for chunk in request.stream():
        if not chunk:
            continue
        written += len(chunk)
        if written > MAX_VIDEO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Match film exceeds the 3 GB upload limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@film_router.get("/upload-film", response_class=HTMLResponse, include_in_schema=False)
async def upload_film_page() -> HTMLResponse:
    """Browser form that streams a film to the inbox in chunks."""

    return HTMLResponse(upload_page_html(""))


@film_router.post(
    "/api/v1/matches/film/chunk",
    response_model=FilmUploadResult,
    summary="Append one 4 MiB chunk of a match film to the inbox",
)
async def upload_match_film_chunk(
    request: Request,
    filename: str = Query(default="match.mp4"),
    offset: int = Query(default=0, ge=0),
    total: int = Query(default=0, ge=0),
    final: bool = Query(default=False),
    x_filename: str | None = Header(default=None, alias="X-Filename"),
) -> FilmUploadResult:
    """Write one chunk to ``{name}.part`` and rename it when ``final`` is set."""

    if total > MAX_VIDEO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Match film exceeds the 3 GB upload limit.",
        )
    chosen = x_filename or filename or "match.mp4"
    destination = _destination_for(chosen)
    part = destination.with_name(destination.name + ".part")
    if offset == 0:
        part.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        existing = 0
    else:
        existing = part.stat().st_size if part.is_file() else 0
        if existing != offset:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Chunk offset {offset} does not match {existing} bytes "
                    "already saved. Start the upload again."
                ),
            )
    try:
        payload = await _read_limited_body(request, already=existing)
    except HTTPException:
        part.unlink(missing_ok=True)
        raise
    if not payload and offset == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded film is empty.",
        )
    with part.open("ab") as out:
        out.write(payload)
    written = part.stat().st_size
    complete = bool(final)
    if complete:
        part.replace(destination)
        written = destination.stat().st_size
        if written <= 0:
            destination.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded film is empty.",
            )
    return FilmUploadResult(
        filename=destination.name,
        path=str(destination if complete else part),
        size_bytes=written,
        inbox=str(destination.parent),
        complete=complete,
    )


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
    """Write the raw request body to ``.local-run/inbox`` in one shot."""

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
    try:
        payload = await _read_limited_body(request)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    if not payload:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded film is empty.",
        )
    destination.write_bytes(payload)
    return FilmUploadResult(
        filename=destination.name,
        path=str(destination),
        size_bytes=len(payload),
        inbox=str(destination.parent),
        complete=True,
    )
