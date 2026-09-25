"""Register a match film URL and save it into the local inbox.

Impact Soccer accepts a public link or a file. EnjoyStats does the same
locally: a direct ``http(s)`` video is streamed to disk (5 GB cap). A
``file://`` or bare path is copied. Public YouTube pages can be fetched
with ``yt-dlp`` when installed. Vimeo page links are not supported —
download the MP4 and upload it (or drop it in the inbox).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import ParseResult, unquote, urlparse

import httpx

from analytics.video_auto_collect import (
    MAX_VIDEO_BYTES,
    TAG_SUFFIXES,
    VIDEO_SUFFIXES,
    VideoCollectError,
    safe_film_name,
    write_film_chunks,
)

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
}
VIMEO_HOSTS = {
    "vimeo.com",
    "www.vimeo.com",
    "player.vimeo.com",
}
UPLOAD_MP4_HINT = "Download the MP4 and upload it, or drop it in the inbox."
VIMEO_UNSUPPORTED = f"Vimeo page links are not supported. {UPLOAD_MP4_HINT}"
LINK_TIMEOUT_S = 120.0
DOWNLOAD_TIMEOUT_S = 3600.0


def register_match_link(url: str, destination_dir: Path) -> Path:
    """Download or copy ``url`` into ``destination_dir`` and return the file.

    Raises:
        VideoCollectError: If the URL is empty, unsupported, or too large.
    """

    raw = url.strip().strip("\u200b")
    if not raw:
        raise VideoCollectError("Paste a match film link or a local file path.")
    destination_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme in {"", "file"} or (scheme not in {"http", "https"} and Path(raw).exists()):
        return _copy_local(raw, parsed, destination_dir)
    if scheme not in {"http", "https"}:
        raise VideoCollectError("Match links must be http(s), file://, or a local path.")
    host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
    if host in VIMEO_HOSTS or host.endswith(".vimeo.com"):
        raise VideoCollectError(VIMEO_UNSUPPORTED)
    if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return _download_page_link(raw, destination_dir)
    return _stream_direct_video(raw, parsed, destination_dir)


def _copy_name(source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix in TAG_SUFFIXES:
        base = (
            "".join(char if char.isalnum() or char in ".-_" else "_" for char in source.name).strip(
                "._"
            )
            or "match.xml"
        )
        if Path(base).suffix.lower() not in TAG_SUFFIXES:
            return f"{base}.xml"
        return base
    return safe_film_name(source.name)


def _copy_local(raw: str, parsed: ParseResult, destination_dir: Path) -> Path:
    if parsed.scheme == "file":
        source = Path(unquote(parsed.path))
        if parsed.netloc and parsed.netloc not in {"localhost", "127.0.0.1"}:
            source = Path(f"//{parsed.netloc}{unquote(parsed.path)}")
    else:
        text = raw
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1]
        source = Path(text).expanduser()
    if not source.is_file():
        raise VideoCollectError(f"Local film not found: {source}")
    dest = destination_dir / _copy_name(source)
    if dest.resolve() != source.resolve():
        shutil.copy2(source, dest)
    return dest


def _stream_direct_video(url: str, parsed: ParseResult, destination_dir: Path) -> Path:
    name = Path(unquote(parsed.path)).name or "match.mp4"
    dest = destination_dir / safe_film_name(name)
    timeout = httpx.Timeout(
        connect=LINK_TIMEOUT_S,
        read=DOWNLOAD_TIMEOUT_S,
        write=LINK_TIMEOUT_S,
        pool=LINK_TIMEOUT_S,
    )
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
            response.raise_for_status()
            ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if (
                ctype
                and not ctype.startswith("video/")
                and ctype
                not in {
                    "application/octet-stream",
                    "application/mp4",
                }
            ):
                raise VideoCollectError(
                    "That link is a web page, not a film file. " + UPLOAD_MP4_HINT
                )
            suffix = Path(safe_film_name(name)).suffix.lower()
            if suffix not in VIDEO_SUFFIXES:
                dest = destination_dir / safe_film_name(f"{Path(name).stem}.mp4")

            def _chunks():
                for chunk in response.iter_bytes(1024 * 1024):
                    if chunk:
                        yield chunk

            return write_film_chunks(dest, _chunks(), max_bytes=MAX_VIDEO_BYTES)
    except httpx.HTTPError as exc:
        raise VideoCollectError(f"Could not download the match link ({exc}).") from exc


def resolve_ytdlp_command() -> list[str] | None:
    """Return an argv prefix that can run yt-dlp, or ``None`` if missing."""

    on_path = shutil.which("yt-dlp")
    if on_path:
        return [on_path]
    exe = Path(sys.executable)
    for candidate in (exe.parent / "yt-dlp", exe.resolve().parent / "yt-dlp"):
        if candidate.is_file():
            return [str(candidate)]
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "yt_dlp"]


def _download_page_link(url: str, destination_dir: Path) -> Path:
    ytdlp = resolve_ytdlp_command()
    if ytdlp is None:
        raise VideoCollectError(
            "YouTube page links need yt-dlp on this machine. " + UPLOAD_MP4_HINT
        )
    dest_tmpl = str(destination_dir / "link-match.%(ext)s")
    command = [
        *ytdlp,
        "--no-playlist",
        "-f",
        "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "-o",
        dest_tmpl,
        "--max-filesize",
        str(MAX_VIDEO_BYTES),
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=DOWNLOAD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoCollectError("The match link download timed out.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace")
        lowered = detail.lower()
        if "logged-in" in lowered or "private" in lowered or "sign in" in lowered:
            raise VideoCollectError(
                "That YouTube film is private or needs a login. " + UPLOAD_MP4_HINT
            )
        snippet = " ".join(detail.strip().splitlines()[-2:])[:200]
        raise VideoCollectError(
            "Could not fetch that page link." + (f" {snippet}" if snippet else "")
        )
    found = sorted(
        destination_dir.glob("link-match.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    videos = [path for path in found if path.suffix.lower() in VIDEO_SUFFIXES]
    if not videos:
        raise VideoCollectError("yt-dlp finished but wrote no match film.")
    return videos[0]
