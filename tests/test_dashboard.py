"""Tests for dashboard dummy fallback, pass directions, and FastAPI client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from app.client import fetch_player_profile
from app.dummy_data import (
    PLAYMAKER_ID,
    SHOWCASE_MATCH_ID,
    SIM_MATCH_ID,
    STRIKER_ID,
    catalog,
    fallback_actions,
    fallback_profile,
)
from app.metrics import PassDirections, directions_from_distribution
from data_models.player_stats import (
    AttemptSplit,
    DistributionStats,
    PassDirectionStats,
    PassLocationStats,
)


def test_catalog_covers_demo_and_showcase_players() -> None:
    rows = catalog()
    assert {row["player_id"] for row in rows} >= {PLAYMAKER_ID, STRIKER_ID}
    assert any(row["match_id"] == SIM_MATCH_ID for row in rows)
    assert any(row["match_id"] == SHOWCASE_MATCH_ID for row in rows)


def test_dummy_playmaker_and_striker_match_pipeline_story() -> None:
    playmaker = fallback_profile(SIM_MATCH_ID, PLAYMAKER_ID)
    striker = fallback_profile(SIM_MATCH_ID, STRIKER_ID)
    assert playmaker.distribution.passes.success_rate == 1.0
    assert playmaker.distribution.passes.total == 3
    assert striker.offensive.goals == 1
    assert striker.offensive.shots_on_target == 1
    assert striker.offensive.shots_inside_penalty_area == 1
    assert striker.defensive.ground_duels.success_rate == 1.0


def test_unknown_ids_still_return_a_valid_profile() -> None:
    profile = fallback_profile(uuid4(), uuid4())
    assert profile.offensive.goals >= 0
    assert profile.distribution.passes.total >= profile.distribution.passes.success


def test_direction_split_uses_explicit_or_derives_from_distribution() -> None:
    explicit = PassDirections(forward=10, sideways=4, backward=2)
    empty = DistributionStats()
    assert directions_from_distribution(empty, explicit=explicit) == explicit
    derived = directions_from_distribution(
        DistributionStats(
            passes=AttemptSplit(success=8, total=10),
            progressive_passes=AttemptSplit(success=3, total=4),
            cutbacks=AttemptSplit(success=1, total=2),
            pass_locations=PassLocationStats(
                short=AttemptSplit(success=4, total=5),
                medium=AttemptSplit(success=3, total=4),
                long=AttemptSplit(success=1, total=1),
            ),
            pass_directions=PassDirectionStats(
                forward=AttemptSplit(success=5, total=6),
                sideways=AttemptSplit(success=2, total=3),
                backward=AttemptSplit(success=1, total=1),
            ),
        )
    )
    assert derived.forward == 6
    assert derived.sideways == 3
    assert derived.backward == 1
    assert derived.as_rows()[0]["Direction"] == "Forward"


@pytest.mark.asyncio
async def test_fetch_falls_back_when_api_is_offline() -> None:
    with patch("app.client.probe_api", new=AsyncMock(return_value=False)):
        load = await fetch_player_profile("http://127.0.0.1:9", SIM_MATCH_ID, PLAYMAKER_ID)
    assert load.source == "fallback"
    assert load.api_online is False
    assert load.profile.player_id == PLAYMAKER_ID
    assert "unreachable" in load.message.lower()
    assert len(load.actions) == 3
    assert all(action.event_type == "pass" for action in load.actions)


@pytest.mark.asyncio
async def test_fetch_uses_live_payload_when_api_returns_profile() -> None:
    live = fallback_profile(SIM_MATCH_ID, STRIKER_ID)
    payload = live.model_dump(mode="json", exclude_computed_fields=True)
    response = httpx.Response(200, json=payload)
    with (
        patch("app.client.probe_api", new=AsyncMock(return_value=True)),
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)),
    ):
        load = await fetch_player_profile("http://127.0.0.1:8000", SIM_MATCH_ID, STRIKER_ID)
    assert load.source == "live"
    assert load.profile.offensive.goals == 1
    assert load.api_online is True
    assert len(load.actions) == 1
    assert load.actions[0].is_goal is True


def test_dashboard_renders_analyse_landing(tmp_path, monkeypatch) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("ENJOYSTATS_FILM_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    script = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    at = AppTest.from_file(str(script), default_timeout=15)
    at.run()
    assert not at.exception
    titles = [str(element.value) for element in at.title]
    assert any("EnjoyStats" in title for title in titles)
    sidebar_headers = [str(element.value) for element in at.sidebar.header]
    assert any("EnjoyStats" in header for header in sidebar_headers)
    nav = next(radio for radio in at.radio if set(radio.options) >= {"Home", "History"})
    assert nav.value == "Home"
    select_labels = [str(element.label) for element in at.selectbox]
    assert not any("Films on this machine" in label for label in select_labels)
    assert not any("Cookies from browser" in label for label in select_labels)
    input_labels = [str(element.label) for element in at.text_input]
    assert not any("Register a link" in label for label in input_labels)
    captions = [str(element.value) for element in at.caption]
    assert any("oncesport" in caption.lower() or "xml" in caption.lower() for caption in captions)
    assert any(
        "chunk" in caption.lower() or "upload" in caption.lower() or "film" in caption.lower()
        for caption in captions
    ) or any("upload match film" in str(element.value).lower() for element in at.markdown)
    buttons = [str(element.label) for element in at.button]
    assert any("Analyse Stats" in label for label in buttons)
    assert any("Napoleon Bot" in label for label in buttons)
    subheaders = [str(element.value) for element in at.subheader]
    assert any("Analyse Stats" in header for header in subheaders)
    expanders = [str(element.label) for element in at.expander]
    assert any("OnceSport" in label or "XML" in label for label in expanders)
    assert any("StatMan" in label or "Grok" in label for label in expanders)
    analyse = next(button for button in at.button if str(button.label) == "Analyse Stats")
    analyse.click().run()
    assert not at.exception
    errors = [str(element.value) for element in at.error]
    infos = [str(element.value) for element in at.info]
    assert any("upload" in message.lower() for message in errors + infos)


def test_analyse_uses_newest_uploaded_film(tmp_path, monkeypatch) -> None:
    from streamlit.testing.v1 import AppTest

    from analytics.collect_job import read_job_status
    from analytics.video_auto_collect import MIN_READY_VIDEO_BYTES

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("ENJOYSTATS_FILM_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    film = inbox / "gw1.mp4"
    header = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42"
    film.write_bytes(header + b"\x00" * MIN_READY_VIDEO_BYTES)
    script = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    at = AppTest.from_file(str(script), default_timeout=20)
    at.run()
    assert not at.exception
    successes = [str(element.value) for element in at.success]
    assert any("gw1.mp4" in message for message in successes)
    analyse = next(button for button in at.button if str(button.label) == "Analyse Stats")
    analyse.click().run()
    assert not at.exception
    job_path = at.session_state.get("collect_job_path")
    assert job_path
    status = read_job_status(Path(str(job_path)))
    assert status is not None
    assert "gw1.mp4" in str(status.get("film") or "")


def test_landing_sample_opens_tag_inventory(tmp_path, monkeypatch) -> None:
    from streamlit.testing.v1 import AppTest

    from analytics.collection_history import list_history

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("ENJOYSTATS_FILM_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    script = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    at = AppTest.from_file(str(script), default_timeout=20)
    at.run()
    assert not at.exception
    sample = next(button for button in at.button if "Napoleon Bot" in str(button.label))
    sample.click().run()
    assert not at.exception
    subheaders = [str(element.value) for element in at.subheader]
    assert "Match rundown" in subheaders
    assert "What you no longer have to tag" in subheaders
    assert "Collective team stats" in subheaders or any(
        "collective" in header.lower() for header in subheaders
    )
    body_bits = " ".join(subheaders)
    assert "Collective" in body_bits or any("Napoleon Bot" in header for header in subheaders)
    downloads = [str(button.label) for button in at.download_button]
    assert any("CSV" in label for label in downloads)
    assert any("XML" in label for label in downloads)
    assert any("PDF" in label for label in downloads)
    history = list_history()
    assert len(history) == 1
    assert "Napoleon" in history[0].label or "Jeans" in history[0].label


def test_collected_rundown_shows_match_tags(tmp_path, monkeypatch) -> None:
    from streamlit.testing.v1 import AppTest

    from analytics.game_ingest import rundown_to_json
    from analytics.sample_game import sample_game_payload
    from analytics.game_ingest import collect_game

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    script = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    at = AppTest.from_file(str(script), default_timeout=20)
    at.session_state["collected_rundown"] = rundown_to_json(collect_game(sample_game_payload()))
    at.run()
    assert not at.exception
    subheaders = [str(element.value) for element in at.subheader]
    assert "Match rundown" in subheaders
    assert "What you no longer have to tag" in subheaders
    assert "Match tags" in subheaders
    assert any(
        "team statistics" in header.lower() or "Team statistics" in header for header in subheaders
    )
    assert any("collective" in header.lower() for header in subheaders)
    assert "Offensive" in subheaders
    assert "Defensive" in subheaders
    assert any(
        "Individual" in header or "Individual players" in header for header in subheaders
    ) or any("Open player sheet" in str(box.label) for box in at.selectbox)
    downloads = [str(button.label) for button in at.download_button]
    assert any("PDF" in label for label in downloads)
    # Home tab returns to Analyse Stats without clearing the match.
    nav = next(
        radio for radio in at.radio if set(radio.options) >= {"Home", "Match rundown", "History"}
    )
    assert nav.value == "Match rundown"
    nav.set_value("Home").run()
    assert not at.exception
    buttons = [str(element.label) for element in at.button]
    assert any("Analyse Stats" in label for label in buttons)
    subheaders_home = [str(element.value) for element in at.subheader]
    assert any("Analyse Stats" in header for header in subheaders_home)


def test_history_tab_lists_saved_collect_and_opens_match(tmp_path, monkeypatch) -> None:
    from streamlit.testing.v1 import AppTest

    from analytics.collection_history import save_rundown_to_history
    from analytics.game_ingest import collect_game
    from analytics.sample_game import sample_game_payload

    monkeypatch.setenv("ENJOYSTATS_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ENJOYSTATS_FILM_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("ENJOYSTATS_FILM_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("ENJOYSTATS_HISTORY_DIR", str(tmp_path / "history"))
    entry = save_rundown_to_history(collect_game(sample_game_payload()))
    script = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    at = AppTest.from_file(str(script), default_timeout=20)
    at.run()
    assert not at.exception
    nav = next(radio for radio in at.radio if set(radio.options) >= {"Home", "History"})
    nav.set_value("History").run()
    assert not at.exception
    subheaders = [str(element.value) for element in at.subheader]
    assert "Collection history" in subheaders
    markdowns = [str(element.value) for element in at.markdown]
    assert any(entry.label in text for text in markdowns)
    downloads = [str(button.label) for button in at.download_button]
    assert any("PDF" in label for label in downloads)
    open_btn = next(button for button in at.button if "Open match" in str(button.label))
    open_btn.click().run()
    assert not at.exception
    subheaders_match = [str(element.value) for element in at.subheader]
    assert "Match rundown" in subheaders_match


def test_unknown_fallback_actions_include_a_missing_coordinate() -> None:
    actions = fallback_actions(uuid4(), uuid4())
    assert any(action.x is None or action.y is None for action in actions)
    assert any(action.event_type == "shot" and action.is_goal for action in actions)
