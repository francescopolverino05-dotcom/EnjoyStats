"""EnjoyStats Streamlit dashboard — three-pillar player-match view.

Launch from the repository root::

    streamlit run app/dashboard.py
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from app.client import DEFAULT_BASE_URL, ProfileLoad, fetch_player_profile
from app.dummy_data import catalog
from app.metrics import PassDirections
from data_models.player_stats import PlayerMatchProfile

FetchFn = Callable[[str, UUID, UUID], ProfileLoad]


def _run_fetch(base_url: str, match_id: UUID, player_id: UUID) -> ProfileLoad:
    """Bridge Streamlit's sync runtime to the async FastAPI client."""

    return asyncio.run(fetch_player_profile(base_url, match_id, player_id))


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
          .block-container { padding-top: 1.4rem; max-width: 1280px; }
          div[data-testid="stMetric"] {
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 14px;
            padding: 0.75rem 0.9rem;
          }
          div[data-testid="stMetric"] label { color: #94a3b8; }
          div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: #f8fafc;
            font-weight: 700;
          }
          h1, h2, h3 { letter-spacing: -0.02em; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _match_options() -> dict[str, UUID]:
    options: dict[str, UUID] = {}
    for row in catalog():
        label = f"{row['match_label']}  ·  {row['match_id']}"
        options[label] = row["match_id"]  # type: ignore[assignment]
    return options


def _player_options(match_id: UUID) -> dict[str, UUID]:
    options: dict[str, UUID] = {}
    for row in catalog():
        if row["match_id"] != match_id:
            continue
        label = f"{row['player_label']}  ·  {row['player_id']}"
        options[label] = row["player_id"]  # type: ignore[assignment]
    return options


def _pct(value: float) -> str:
    return f"{value:.0%}"


def render_offensive(profile: PlayerMatchProfile) -> None:
    """High-level finishing boxes for the offensive pillar."""

    st.subheader("Offensive")
    st.caption("Goals, chance creation, and shot geography.")
    offensive = profile.offensive
    top = st.columns(2)
    top[0].metric("Goals", offensive.goals)
    top[1].metric("Assists", offensive.assists)
    bottom = st.columns(2)
    bottom[0].metric("Shots on target", offensive.shots_on_target)
    bottom[1].metric(
        "Inside / outside PA",
        f"{offensive.shots_inside_penalty_area} / {offensive.shots_outside_penalty_area}",
    )
    st.caption(
        f"Minutes {offensive.minutes:.1f}  ·  "
        f"{offensive.total_shots} total shots  ·  "
        f"accuracy {_pct(offensive.shot_accuracy)}"
    )


def render_defensive(profile: PlayerMatchProfile) -> None:
    """Duel rates and recoveries by tactical third."""

    st.subheader("Defensive")
    st.caption("Duel success and zonal ball recoveries.")
    defensive = profile.defensive
    ground = defensive.ground_duels
    aerial = defensive.aerial_duels
    c1, c2 = st.columns(2)
    c1.metric("Ground duel win %", _pct(ground.success_rate), f"{ground.success}/{ground.total}")
    c2.metric("Aerial duel win %", _pct(aerial.success_rate), f"{aerial.success}/{aerial.total}")
    c1.progress(ground.success_rate)
    c2.progress(aerial.success_rate)
    recoveries = defensive.ball_recoveries
    st.markdown("**Ball recoveries by tactical third**")
    st.dataframe(
        [
            {"Third": "Defensive", "Recoveries": recoveries.defensive_third},
            {"Third": "Middle", "Recoveries": recoveries.middle_third},
            {"Third": "Final", "Recoveries": recoveries.final_third},
        ],
        hide_index=True,
        width="stretch",
    )


def render_distribution(profile: PlayerMatchProfile, directions: PassDirections) -> None:
    """Pass accuracy plus forward / sideways / backward breakdown."""

    st.subheader("Distribution")
    st.caption("Passing accuracy and direction of travel.")
    passing = profile.distribution.passes
    st.metric(
        "Pass accuracy",
        _pct(passing.success_rate),
        f"{passing.success}/{passing.total} completed",
    )
    st.markdown("**Direction of distribution**")
    d1, d2, d3 = st.columns(3)
    d1.metric("Forward", directions.forward)
    d2.metric("Sideways", directions.sideways)
    d3.metric("Backward", directions.backward)
    st.dataframe(
        [
            {
                "Direction": row["Direction"],
                "Passes": row["Passes"],
                "Share": _pct(float(row["Share"])),
            }
            for row in directions.as_rows()
        ],
        hide_index=True,
        width="stretch",
    )


def render_dashboard(load: ProfileLoad) -> None:
    """Compose the three-pillar layout for one loaded profile."""

    profile = load.profile
    header_l, header_r = st.columns([3, 1])
    with header_l:
        jersey = f"#{profile.jersey_number} " if profile.jersey_number else ""
        st.title("EnjoyStats")
        st.markdown("### Player match dashboard")
        st.write(
            f"{jersey}{profile.position or 'Player'}  ·  "
            f"match `{profile.match_id}`  ·  player `{profile.player_id}`"
        )
    with header_r:
        if load.source == "live":
            st.success("Live FastAPI")
        else:
            st.warning("Dummy fallback")
        st.caption(load.message)

    offensive_col, defensive_col, distribution_col = st.columns(3)
    with offensive_col:
        render_offensive(profile)
    with defensive_col:
        render_defensive(profile)
    with distribution_col:
        render_distribution(profile, load.directions)


def render_sidebar() -> tuple[str, UUID, UUID]:
    """Match / player selectors that drive the GET request."""

    st.sidebar.header("Match selection")
    st.sidebar.caption("Choosing a match or player loads `/api/v1/matches/{id}/players/{id}`.")
    base_url = st.sidebar.text_input("FastAPI base URL", value=DEFAULT_BASE_URL)
    matches = _match_options()
    match_label = st.sidebar.selectbox("Match UUID", options=list(matches.keys()))
    match_id = matches[match_label]
    players = _player_options(match_id)
    player_label = st.sidebar.selectbox("Player ID", options=list(players.keys()))
    player_id = players[player_label]
    st.sidebar.divider()
    st.sidebar.caption("If FastAPI is down, dummy values for this selection still render.")
    return base_url.strip() or DEFAULT_BASE_URL, match_id, player_id


def main(*, fetch: FetchFn = _run_fetch) -> None:
    """Streamlit entry point. ``fetch`` is injectable for tests."""

    st.set_page_config(
        page_title="EnjoyStats · Player dashboard",
        page_icon="⚽",
        layout="wide",
    )
    _inject_styles()
    base_url, match_id, player_id = render_sidebar()
    load = fetch(base_url, match_id, player_id)
    render_dashboard(load)


if __name__ == "__main__":
    main()
