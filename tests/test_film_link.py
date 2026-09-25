"""Register a match film link into the local inbox."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from analytics.film_link import (
    VIMEO_LOGIN_HINT,
    register_match_link,
    resolve_ytdlp_command,
)
from analytics.video_auto_collect import VideoCollectError


def test_register_rejects_empty_link(tmp_path: Path) -> None:
    with pytest.raises(VideoCollectError, match="link"):
        register_match_link("   ", tmp_path)


def test_resolve_ytdlp_finds_installed_module() -> None:
    command = resolve_ytdlp_command()
    assert command is not None
    assert command[0]
    assert "yt-dlp" in " ".join(command) or "yt_dlp" in " ".join(command)


def test_youtube_link_without_ytdlp_is_clear(tmp_path: Path) -> None:
    with patch("analytics.film_link.resolve_ytdlp_command", return_value=None):
        with pytest.raises(VideoCollectError, match="yt-dlp"):
            register_match_link("https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)


def test_vimeo_login_error_explains_cookies(tmp_path: Path) -> None:
    completed = MagicMock()
    completed.returncode = 1
    completed.stderr = (
        b"ERROR: [vimeo] 1224195986: The web client only works when logged-in. "
        b"Use --cookies, --cookies-from-browser, --username and --password"
    )
    completed.stdout = b""
    with (
        patch("analytics.film_link.resolve_ytdlp_command", return_value=["yt-dlp"]),
        patch("analytics.film_link.subprocess.run", return_value=completed) as run,
    ):
        with pytest.raises(VideoCollectError, match="logged-in session"):
            register_match_link("https://vimeo.com/1224195986", tmp_path)
    command = run.call_args.args[0]
    assert "--cookies-from-browser" not in command


def test_vimeo_passes_browser_cookies_and_password(tmp_path: Path) -> None:
    clip = tmp_path / "link-match.mp4"
    clip.write_bytes(b"film")

    def _fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        assert "--cookies-from-browser" in command
        assert "chrome" in command
        assert "--video-password" in command
        assert "secret" in command
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = b""
        completed.stdout = b""
        return completed

    with (
        patch("analytics.film_link.resolve_ytdlp_command", return_value=["yt-dlp"]),
        patch("analytics.film_link.subprocess.run", side_effect=_fake_run),
    ):
        dest = register_match_link(
            "https://vimeo.com/1224195986",
            tmp_path,
            cookies_from_browser="chrome",
            video_password="secret",
        )
    assert dest == clip


def test_vimeo_uses_cookies_file(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape\n", encoding="utf-8")
    clip = tmp_path / "inbox" / "link-match.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"film")

    def _fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        assert "--cookies" in command
        assert str(cookies) in command
        assert "--cookies-from-browser" not in command
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = b""
        completed.stdout = b""
        return completed

    with (
        patch("analytics.film_link.resolve_ytdlp_command", return_value=["yt-dlp"]),
        patch("analytics.film_link.subprocess.run", side_effect=_fake_run),
    ):
        dest = register_match_link(
            "https://vimeo.com/1224195986",
            clip.parent,
            cookies_file=cookies,
        )
    assert dest == clip
    assert VIMEO_LOGIN_HINT.startswith("This Vimeo film")


def test_register_copies_local_path_and_file_uri(tmp_path: Path) -> None:
    source = tmp_path / "derby.mp4"
    source.write_bytes(b"film-bytes")
    dest_dir = tmp_path / "inbox"
    copied = register_match_link(str(source), dest_dir)
    assert copied.is_file()
    assert copied.read_bytes() == b"film-bytes"
    via_uri = register_match_link(source.resolve().as_uri(), dest_dir)
    assert via_uri == copied


def test_register_preserves_official_xml_suffix(tmp_path: Path) -> None:
    source = tmp_path / "Arsenal v Palace.xml"
    source.write_text("<analysis/>", encoding="utf-8")
    dest = register_match_link(f'"{source}"', tmp_path / "inbox")
    assert dest.suffix.lower() == ".xml"
    assert dest.read_text(encoding="utf-8") == "<analysis/>"


def test_register_rejects_unknown_scheme(tmp_path: Path) -> None:
    with pytest.raises(VideoCollectError, match="http"):
        register_match_link("ftp://example.test/match.mp4", tmp_path)
