"""Register a match film URL and save it into the local inbox.

Impact Soccer accepts a public link or a file. EnjoyStats does the same
locally: a direct ``http(s)`` video is streamed to disk (5 GB cap). A
``file://`` or bare path is copied. Hosted pages (YouTube, Vimeo) are
fetched with ``yt-dlp`` when that tool is installed — only for film the
operator has the right to analyse.

Private Vimeo / logged-in pages need session cookies (browser cookies on
this machine, or a Netscape ``cookies.txt``), or a video password when
the share is password-gated.
"""

from __future__ import annotations

import os
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

PAGE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "vimeo.com",
    "www.vimeo.com",
    "player.vimeo.com",
}
BROWSER_COOKIE_CHOICES = (
    "chrome",
    "chromium",
    "firefox",
    "edge",
    "safari",
    "brave",
    "opera",
)
LINK_TIMEOUT_S = 120.0
DOWNLOAD_TIMEOUT_S = 3600.0
VIMEO_LOGIN_HINT = (
    "This Vimeo film needs a logged-in session. "
    "Under Register a link, pick Cookies from browser "
    "(Chrome on this computer where you are logged into Vimeo), "
    "or upload a Netscape cookies.txt. "
    "Or download the MP4 yourself and drop it in the inbox / upload it."
)


def register_match_link(
    url: str,
    destination_dir: Path,
    *,
    cookies_file: Path | str | None = None,
    cookies_from_browser: str | None = None,
    video_password: str | None = None,
) -> Path:
    """Download or copy ``url`` into ``destination_dir`` and return the file.

    Args:
        url: Direct video URL, local path, ``file://``, or YouTube/Vimeo page.
        destination_dir: Inbox folder that receives the film.
        cookies_file: Netscape cookies file for logged-in Vimeo / YouTube.
        cookies_from_browser: Browser name for ``yt-dlp --cookies-from-browser``.
        video_password: Password for a password-gated Vimeo share.

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
    auth = _resolve_ytdlp_auth(
        cookies_file=cookies_file,
        cookies_from_browser=cookies_from_browser,
        video_password=video_password,
    )
    if host in PAGE_HOSTS or host.endswith(".youtube.com"):
        return _download_page_link(raw, destination_dir, auth=auth)
    return _stream_direct_video(raw, parsed, destination_dir)


def _resolve_ytdlp_auth(
    *,
    cookies_file: Path | str | None,
    cookies_from_browser: str | None,
    video_password: str | None,
) -> dict[str, str | None]:
    """Merge UI auth with env defaults for private page downloads."""

    file_raw = (
        str(cookies_file).strip()
        if cookies_file is not None
        else (os.environ.get("ENJOYSTATS_YTDLP_COOKIES") or "").strip()
    )
    browser_raw = (
        (cookies_from_browser or "").strip().lower()
        or (os.environ.get("ENJOYSTATS_YTDLP_BROWSER") or "").strip().lower()
    )
    password_raw = (
        (video_password or "").strip()
        or (os.environ.get("ENJOYSTATS_YTDLP_PASSWORD") or "").strip()
    )
    if browser_raw in {"", "none", "(none)"}:
        browser_raw = ""
    if browser_raw and browser_raw not in BROWSER_COOKIE_CHOICES:
        raise VideoCollectError(
            "Cookies from browser must be one of: "
            + ", ".join(BROWSER_COOKIE_CHOICES)
            + "."
        )
    cookies_path: str | None = None
    if file_raw:
        path = Path(file_raw).expanduser()
        if not path.is_file():
            raise VideoCollectError(f"Cookies file not found: {path}")
        cookies_path = str(path)
    return {
        "cookies_file": cookies_path,
        "cookies_from_browser": browser_raw or None,
        "video_password": password_raw or None,
    }


def _copy_name(source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix in TAG_SUFFIXES:
        base = "".join(
            char if char.isalnum() or char in ".-_" else "_" for char in source.name
        ).strip("._") or "match.xml"
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
            if ctype and not ctype.startswith("video/") and ctype not in {
                "application/octet-stream",
                "application/mp4",
            }:
                raise VideoCollectError(
                    "That link is a web page, not a film file. Drop the MP4 "
                    "into the inbox, or install yt-dlp for YouTube/Vimeo."
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
    """Return an argv prefix that can run yt-dlp, or ``None`` if missing.

    Prefers the ``yt-dlp`` binary on ``PATH``, then the one next to the
    active interpreter (venv), then ``python -m yt_dlp``.
    """

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


def _ytdlp_auth_args(auth: dict[str, str | None]) -> list[str]:
    args: list[str] = []
    cookies_file = auth.get("cookies_file")
    browser = auth.get("cookies_from_browser")
    password = auth.get("video_password")
    if cookies_file:
        args.extend(["--cookies", cookies_file])
    elif browser:
        args.extend(["--cookies-from-browser", browser])
    if password:
        args.extend(["--video-password", password])
    return args


def _login_required_message(detail: str) -> str | None:
    lowered = detail.lower()
    if "logged-in" in lowered or "cookies-from-browser" in lowered or "use --cookies" in lowered:
        return VIMEO_LOGIN_HINT
    if "password" in lowered and "vimeo" in lowered:
        return (
            "That Vimeo share needs a video password. "
            "Enter it under Register a link → Vimeo / YouTube login, "
            "or download the MP4 and upload it."
        )
    return None


def _download_page_link(
    url: str,
    destination_dir: Path,
    *,
    auth: dict[str, str | None] | None = None,
) -> Path:
    ytdlp = resolve_ytdlp_command()
    if ytdlp is None:
        raise VideoCollectError(
            "YouTube/Vimeo links need yt-dlp on this machine. "
            "Drop the MP4 into the inbox, or paste a direct video URL."
        )
    resolved_auth = auth or {
        "cookies_file": None,
        "cookies_from_browser": None,
        "video_password": None,
    }
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
        *_ytdlp_auth_args(resolved_auth),
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
        login_hint = _login_required_message(detail)
        if login_hint is not None:
            raise VideoCollectError(login_hint)
        snippet = " ".join(detail.strip().splitlines()[-2:])[:240]
        raise VideoCollectError(
            "Could not fetch that page link."
            + (f" {snippet}" if snippet else "")
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
