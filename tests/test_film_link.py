"""Register a match film link into the local inbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from analytics.film_link import register_match_link
from analytics.video_auto_collect import VideoCollectError


def test_register_rejects_empty_link(tmp_path: Path) -> None:
    with pytest.raises(VideoCollectError, match="link"):
        register_match_link("   ", tmp_path)


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
