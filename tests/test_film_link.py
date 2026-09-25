"""Register a match film link into the local inbox."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from analytics.film_link import (
    is_vimeo_page_link,
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


def test_vimeo_page_is_detected_and_not_fetched(tmp_path: Path) -> None:
    url = "https://vimeo.com/1224195986"
    assert is_vimeo_page_link(url)
    with pytest.raises(VideoCollectError, match="upload the MP4"):
        register_match_link(url, tmp_path)


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
