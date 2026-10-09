"""StatMan Streamlit dashboard — Analyse Stats plus collective team pillars.

Launch from the repository root::

    streamlit run app/dashboard.py
"""

from __future__ import annotations

import asyncio
import importlib
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import streamlit as st
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse, Rectangle

from app.client import DEFAULT_BASE_URL, ProfileLoad, fetch_player_profile, probe_api
from app.dummy_data import PitchAction, catalog
from app.metrics import PassDirections

import analytics.match_tags as _match_tags_mod
import app.ingest as _ingest_mod
import analytics.team_collect as _team_collect_mod

importlib.reload(_match_tags_mod)
importlib.reload(_ingest_mod)
importlib.reload(_team_collect_mod)
from app.ingest import (
    UPLOAD_DISCONNECT_HINT,
    collect_from_film_path,
    collect_sample_match,
    collect_uploaded_bytes,
    film_has_official_tags,
    latest_ready_film,
    load_from_rundown,
    load_from_team_profile,
    persist_rundown,
    profile_label,
    ready_match_films,
    save_uploaded_film,
)
from analytics.collect_job import (
    latest_job_status_path,
    load_job_rundown,
    read_job_status,
    start_collect_job,
)
from analytics.team_collect import (
    SheetPerspective,
    analysed_team_profile,
    analysis_perspective,
    named_player_profiles,
    team_profiles_from_rundown,
)
from analytics.video_auto_collect import (
    VIDEO_SUFFIXES,
    VideoCollectError,
    film_inbox_dir,
    film_upload_dir,
    safe_film_name,
)
from api.film_upload import upload_page_html
from api.supervisor import api_is_healthy, ensure_api_running
from analytics.collection_history import (
    delete_history_entry,
    list_history,
    load_history_rundown,
    save_rundown_to_history,
)
from analytics.game_ingest import MatchRundown, rundown_from_mapping, rundown_to_json
from analytics.match_report_pdf import build_match_report_pdf
from analytics.team_sheet import (
    highlight_moments_from_rundown,
    tag_inventory_rows,
    team_sheet_rows,
    team_sheets_from_rundown,
)
from analytics.batch_collect import list_inbox_films, start_batch_collect, summarize_batch
from analytics.distinti import facts_to_payload, parse_distinti_pdf
from analytics.lineups import example_lineup_csv, parse_lineup_bytes
from analytics.match_tags import attacks_from_events, rundown_to_csv, rundown_to_xml
from analytics.oncesport_export import export_both_oncesport_xml
from analytics.review_edits import (
    apply_review_edits,
    coverage_rows_for_rundown,
    event_type_choices,
    player_label_map,
    review_rows,
)
from config.pitch_config import (
    CENTRE_CIRCLE_RADIUS_M,
    FIFA_PITCH,
    NORMALIZED_MAX,
    NORMALIZED_MIN,
    PENALTY_SPOT_DISTANCE_M,
    PitchDimensions,
)
from data_models.player_stats import AttemptSplit, PlayerMatchProfile

FetchFn = Callable[[str, UUID, UUID], ProfileLoad]

FIELD_GREEN = "#2e7d32"
FIELD_EDGE = "#145214"
LINE_COLOR = "#f8fafc"
LINEWIDTH = 1.7
PASS_SUCCESS_COLOR = "#90caf9"
PASS_FAILED_COLOR = "#ef9a9a"
GOAL_COLOR = "#76ff03"
ON_TARGET_COLOR = "#ffd54f"
MISSED_COLOR = "#ff1744"
BLOCKED_COLOR = "#b0bec5"


def has_valid_pitch_point(x: float | None, y: float | None) -> bool:
    """Return whether ``(x, y)`` is a finite point on the closed 0–100 grid."""

    if x is None or y is None:
        return False
    try:
        xf = float(x)
        yf = float(y)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(xf) or not math.isfinite(yf):
        return False
    return NORMALIZED_MIN <= xf <= NORMALIZED_MAX and NORMALIZED_MIN <= yf <= NORMALIZED_MAX


def draw_football_pitch(
    ax: Axes | None = None,
    *,
    pitch: PitchDimensions = FIFA_PITCH,
) -> tuple[Figure, Axes]:
    """Draw a FIFA pitch on the normalized 0–100 tagging grid.

    Touchlines, the halfway line, and both 18-yard penalty boxes are mapped
    from :data:`~config.pitch_config.FIFA_PITCH` so scatter points share the
    same coordinate system as live tagged events.

    Args:
        ax: Optional existing axes. When omitted a new figure is created.
        pitch: Physical pitch whose 0–100 projection is drawn.

    Returns:
        The matplotlib ``(figure, axes)`` pair. X is attacking length
        (own goal = 0, opponent = 100); Y is width (left touchline = 0).
    """

    sns.set_theme(style="white", rc={"axes.grid": False, "figure.facecolor": FIELD_EDGE})
    if ax is None:
        fig, ax = plt.subplots(figsize=(12.0, 7.8), dpi=120)
    else:
        fig = ax.figure

    ax.set_xlim(NORMALIZED_MIN, NORMALIZED_MAX)
    ax.set_ylim(NORMALIZED_MIN, NORMALIZED_MAX)
    ax.set_aspect(pitch.width_m / pitch.length_m)
    ax.set_facecolor(FIELD_GREEN)
    fig.patch.set_facecolor(FIELD_EDGE)
    ax.set_xlabel("Attacking length  ·  0 = own goal, 100 = opponent")
    ax.set_ylabel("Width  ·  0 = left touchline, 100 = right")
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.tick_params(colors="#d1fae5", labelsize=8)
    ax.xaxis.label.set_color("#d1fae5")
    ax.yaxis.label.set_color("#d1fae5")
    for spine in ax.spines.values():
        spine.set_color(LINE_COLOR)
        spine.set_linewidth(LINEWIDTH)

    span = NORMALIZED_MAX - NORMALIZED_MIN
    ax.add_patch(
        Rectangle(
            (NORMALIZED_MIN, NORMALIZED_MIN),
            span,
            span,
            fill=False,
            edgecolor=LINE_COLOR,
            linewidth=LINEWIDTH,
            zorder=2,
        )
    )
    ax.plot(
        [50.0, 50.0],
        [NORMALIZED_MIN, NORMALIZED_MAX],
        color=LINE_COLOR,
        linewidth=LINEWIDTH,
        solid_capstyle="butt",
        zorder=2,
    )

    centre_rx = (CENTRE_CIRCLE_RADIUS_M / pitch.length_m) * span
    centre_ry = (CENTRE_CIRCLE_RADIUS_M / pitch.width_m) * span
    ax.add_patch(
        Ellipse(
            (50.0, 50.0),
            width=2.0 * centre_rx,
            height=2.0 * centre_ry,
            fill=False,
            edgecolor=LINE_COLOR,
            linewidth=LINEWIDTH,
            zorder=2,
        )
    )
    ax.scatter([50.0], [50.0], s=16, color=LINE_COLOR, zorder=3)

    box_y_min = pitch.penalty_area_y_min_norm
    box_height = pitch.penalty_area_y_max_norm - box_y_min
    box_depth = pitch.penalty_area_depth_norm
    ax.add_patch(
        Rectangle(
            (NORMALIZED_MIN, box_y_min),
            box_depth,
            box_height,
            fill=False,
            edgecolor=LINE_COLOR,
            linewidth=LINEWIDTH,
            zorder=2,
        )
    )
    ax.add_patch(
        Rectangle(
            (pitch.attacking_penalty_x_min_norm, box_y_min),
            box_depth,
            box_height,
            fill=False,
            edgecolor=LINE_COLOR,
            linewidth=LINEWIDTH,
            zorder=2,
        )
    )

    goal_area_depth = (pitch.goal_area_depth_m / pitch.length_m) * span
    goal_area_y_min = ((pitch.width_m - pitch.goal_area_width_m) / (2.0 * pitch.width_m)) * span
    goal_area_height = span - (2.0 * goal_area_y_min)
    ax.add_patch(
        Rectangle(
            (NORMALIZED_MIN, goal_area_y_min),
            goal_area_depth,
            goal_area_height,
            fill=False,
            edgecolor=LINE_COLOR,
            linewidth=1.2,
            zorder=2,
        )
    )
    ax.add_patch(
        Rectangle(
            (NORMALIZED_MAX - goal_area_depth, goal_area_y_min),
            goal_area_depth,
            goal_area_height,
            fill=False,
            edgecolor=LINE_COLOR,
            linewidth=1.2,
            zorder=2,
        )
    )

    spot_x = (PENALTY_SPOT_DISTANCE_M / pitch.length_m) * span
    ax.scatter(
        [spot_x, NORMALIZED_MAX - spot_x],
        [50.0, 50.0],
        s=16,
        color=LINE_COLOR,
        zorder=3,
    )
    return fig, ax


def _action_kind(action: PitchAction) -> str:
    return action.event_type.strip().lower()


def _is_goal(action: PitchAction) -> bool:
    if action.is_goal:
        return True
    outcome = (action.shot_outcome or "").strip().lower()
    return _action_kind(action) == "goal" or outcome == "goal"


def _shot_category(action: PitchAction) -> str:
    if _is_goal(action):
        return "goal"
    outcome = (action.shot_outcome or "").strip().lower()
    if outcome == "missed" or (outcome == "" and not action.successful):
        return "missed"
    if outcome == "blocked":
        return "blocked"
    if outcome == "on_target" or action.successful:
        return "on_target"
    return "missed"


def scatter_match_actions(
    ax: Axes,
    actions: Sequence[PitchAction],
) -> tuple[int, int]:
    """Plot shot and pass locations, skipping incomplete coordinates.

    Successful goals are green circles; missed shots are red X markers.
    Completed passes are light-blue circles with arrows to the end point
    when both end coordinates are valid.

    Args:
        ax: Pitch axes produced by :func:`draw_football_pitch`.
        actions: Tagged locations for the selected player.

    Returns:
        ``(plotted, skipped)`` counts. ``skipped`` includes tags whose start
        point is missing, non-finite, or outside the 0–100 grid.
    """

    plotted = 0
    skipped = 0
    buckets: dict[str, list[tuple[float, float]]] = {
        "goal": [],
        "on_target": [],
        "missed": [],
        "blocked": [],
        "pass_ok": [],
        "pass_fail": [],
    }
    arrows: list[tuple[float, float, float, float, str]] = []

    for action in actions:
        kind = _action_kind(action)
        if not has_valid_pitch_point(action.x, action.y):
            skipped += 1
            continue
        x = float(action.x)
        y = float(action.y)
        if kind in {"shot", "goal"}:
            buckets[_shot_category(action)].append((x, y))
            plotted += 1
            continue
        if kind in {"pass", "cross", "cutback", "assist"}:
            bucket = "pass_ok" if action.successful else "pass_fail"
            buckets[bucket].append((x, y))
            plotted += 1
            if has_valid_pitch_point(action.end_x, action.end_y):
                color = PASS_SUCCESS_COLOR if action.successful else PASS_FAILED_COLOR
                arrows.append((x, y, float(action.end_x), float(action.end_y), color))
            continue
        skipped += 1

    styles: tuple[tuple[str, str, str, str, float], ...] = (
        ("pass_ok", PASS_SUCCESS_COLOR, "o", "Completed pass", 70.0),
        ("pass_fail", PASS_FAILED_COLOR, "o", "Incomplete pass", 70.0),
        ("on_target", ON_TARGET_COLOR, "o", "Shot on target", 90.0),
        ("blocked", BLOCKED_COLOR, "D", "Blocked shot", 70.0),
        ("missed", MISSED_COLOR, "x", "Missed shot", 110.0),
        ("goal", GOAL_COLOR, "o", "GOAL", 150.0),
    )
    for key, color, marker, label, size in styles:
        points = buckets[key]
        if not points:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        sns.scatterplot(
            x=xs,
            y=ys,
            ax=ax,
            color=color,
            marker=marker,
            s=size,
            legend=False,
            zorder=5 if key in {"goal", "missed"} else 4,
            edgecolor="white" if marker in {"o", "D"} else None,
            linewidth=0.9 if marker in {"o", "D"} else 1.6,
            label=label,
        )

    for start_x, start_y, end_x, end_y, color in arrows:
        ax.annotate(
            "",
            xy=(end_x, end_y),
            xytext=(start_x, start_y),
            arrowprops={
                "arrowstyle": "-|>",
                "color": color,
                "lw": 1.2,
                "mutation_scale": 10,
            },
            zorder=3,
        )

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        legend = ax.legend(
            handles,
            labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.08),
            ncol=min(len(labels), 4),
            frameon=True,
            fontsize=8,
        )
        legend.get_frame().set_facecolor("#145214")
        legend.get_frame().set_edgecolor(LINE_COLOR)
        for text in legend.get_texts():
            text.set_color(LINE_COLOR)
    return plotted, skipped


def _run_fetch(base_url: str, match_id: UUID, player_id: UUID) -> ProfileLoad:
    """Bridge Streamlit's sync runtime to the async FastAPI client."""

    return asyncio.run(fetch_player_profile(base_url, match_id, player_id))


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
          html { -webkit-text-size-adjust: 100%; }
          .stApp { overflow-x: hidden; }
          .stDeployButton, div[data-testid="stToolbar"] { display: none !important; }
          footer { visibility: hidden; }
          .block-container {
            padding-top: 1.1rem;
            padding-bottom: max(2.4rem, env(safe-area-inset-bottom));
            padding-left: max(1.1rem, env(safe-area-inset-left));
            padding-right: max(1.1rem, env(safe-area-inset-right));
            max-width: 1280px;
          }
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
          div[data-testid="stRadio"] {
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 14px;
            padding: 0.35rem 0.7rem;
            margin-bottom: 0.85rem;
          }
          div[data-testid="stRadio"] label {
            color: #e2e8f0 !important;
            font-weight: 600;
          }
          div[data-testid="stButton"] button {
            min-height: 48px;
            border-radius: 12px;
          }
          div[data-testid="stButton"] button[kind="primary"],
          section.main [data-testid="stBaseButton-primary"] {
            width: 100%;
            font-weight: 700;
            background: #16a34a !important;
            color: #fff !important;
            border: 0 !important;
          }
          div[data-testid="stTextInput"] input,
          div[data-testid="stSelectbox"] div[data-baseweb="select"],
          div[data-testid="stFileUploader"] section {
            min-height: 44px;
            font-size: 16px !important;
          }
          div[data-testid="stDataFrame"] { overflow-x: auto; -webkit-overflow-scrolling: touch; }
          @media (max-width: 640px) {
            .block-container {
              padding-top: 0.7rem;
              padding-left: max(0.65rem, env(safe-area-inset-left));
              padding-right: max(0.65rem, env(safe-area-inset-right));
              max-width: 100%;
            }
            h1 { font-size: 1.55rem !important; }
            h2 { font-size: 1.2rem !important; }
            h3 { font-size: 1.05rem !important; }
            div[data-testid="stMetric"] { padding: 0.55rem 0.65rem; }
            div[data-testid="stMetric"] [data-testid="stMetricValue"] {
              font-size: 1.15rem;
            }
          }
          @media (min-width: 641px) and (max-width: 1024px) {
            .block-container { max-width: 960px; }
          }
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


def _split_rows(*pairs: tuple[str, AttemptSplit]) -> list[dict[str, object]]:
    return [
        {
            "Stat": label,
            "Success": split.success,
            "Total": split.total,
            "Accuracy": _pct(split.success_rate),
        }
        for label, split in pairs
    ]


def render_offensive(profile: PlayerMatchProfile) -> None:
    """Finishing, set pieces, and shot geography."""

    st.subheader("Offensive")
    st.caption("Goals, chance creation, shooting, and restarts.")
    offensive = profile.offensive
    identity = st.columns(2)
    identity[0].metric("Minutes played", f"{offensive.minutes:.1f}")
    if profile.position == "TEAM":
        identity[1].metric("Team", profile.player_name or "Team")
    else:
        identity[1].metric(
            "Number / name",
            (
                f"#{profile.jersey_number or '—'}"
                f" {profile.player_name or profile.position or 'Player'}"
            ),
        )
    top = st.columns(2)
    top[0].metric("Goals", offensive.goals)
    top[1].metric("Assists", offensive.assists)
    shots = st.columns(2)
    shots[0].metric("Total shots", offensive.total_shots)
    shots[1].metric("Shots on target", offensive.shots_on_target)
    more = st.columns(2)
    more[0].metric("Shooting accuracy", _pct(offensive.shot_accuracy))
    more[1].metric(
        "Inside / outside PA",
        f"{offensive.shots_inside_penalty_area} / {offensive.shots_outside_penalty_area}",
    )
    rest = st.columns(2)
    rest[0].metric("Shots blocked", offensive.blocked_shots)
    rest[1].metric("Shots missed", offensive.missed_shots)
    set_pieces = st.columns(2)
    set_pieces[0].metric("Offsides", offensive.offsides)
    set_pieces[1].metric("Penalty kicks", offensive.penalty_kicks)
    restarts = st.columns(3)
    restarts[0].metric("Freekicks", offensive.freekicks)
    restarts[1].metric("Corners", offensive.corners)
    restarts[2].metric("Throw-ins", offensive.throw_ins)


def render_defensive(profile: PlayerMatchProfile) -> None:
    """Duels, recoveries, cards, and pressing."""

    st.subheader("Defensive")
    st.caption("Duels, recoveries, turnovers, cards, and pressing intensity.")
    defensive = profile.defensive
    ground = defensive.ground_duels
    aerial = defensive.aerial_duels
    c1, c2 = st.columns(2)
    c1.metric("Ground duel win %", _pct(ground.success_rate), f"{ground.success}/{ground.total}")
    c2.metric("Aerial duel win %", _pct(aerial.success_rate), f"{aerial.success}/{aerial.total}")
    c1.progress(ground.success_rate)
    c2.progress(aerial.success_rate)
    cards = st.columns(3)
    cards[0].metric("Yellow cards", defensive.yellow_cards)
    cards[1].metric("Red cards", defensive.red_cards)
    cards[2].metric("Goals against", defensive.goals_against)
    press = st.columns(2)
    press[0].metric("PPDA", f"{defensive.ppda:.1f}")
    intercepts = defensive.interceptions
    press[1].metric(
        "Interceptions",
        intercepts.total,
        f"{intercepts.defensive_third}/{intercepts.middle_third}/{intercepts.final_third}",
    )
    fouls = st.columns(2)
    fouls[0].metric("Fouls", defensive.fouls.committed)
    fouls[1].metric("Fouls won", defensive.fouls.won)
    blocks = defensive.blocks
    st.markdown("**Blocks**")
    st.dataframe(
        [
            {"Type": "Shots", "Blocks": blocks.shots},
            {"Type": "Crosses", "Blocks": blocks.crosses},
            {"Type": "Passes", "Blocks": blocks.passes},
            {"Type": "Total", "Blocks": blocks.total},
        ],
        hide_index=True,
        width="stretch",
    )
    recoveries = defensive.ball_recoveries
    lost = defensive.ball_lost
    st.markdown("**Recoveries and balls lost by third**")
    st.dataframe(
        [
            {
                "Third": "Defensive",
                "Recoveries": recoveries.defensive_third,
                "Ball lost": lost.defensive_third,
            },
            {
                "Third": "Middle",
                "Recoveries": recoveries.middle_third,
                "Ball lost": lost.middle_third,
            },
            {
                "Third": "Final",
                "Recoveries": recoveries.final_third,
                "Ball lost": lost.final_third,
            },
        ],
        hide_index=True,
        width="stretch",
    )


def render_distribution(profile: PlayerMatchProfile, directions: PassDirections) -> None:
    """Pass accuracy, thirds, length, direction, and chance creation."""

    st.subheader("Distribution")
    st.caption("Passing by third, length, direction, and destination.")
    dist = profile.distribution
    passing = dist.passes
    st.metric(
        "Pass accuracy",
        _pct(passing.success_rate),
        f"{passing.success}/{passing.total} completed",
    )
    st.markdown("**Passes by tactical third**")
    st.dataframe(
        _split_rows(
            ("Defensive third", dist.pass_thirds.defensive_third),
            ("Middle third", dist.pass_thirds.middle_third),
            ("Final third", dist.pass_thirds.final_third),
            ("Into final third", dist.into_final_third),
            ("Into PA", dist.pass_locations.into_penalty_area),
        ),
        hide_index=True,
        width="stretch",
    )
    st.markdown("**Pass length**")
    st.dataframe(
        _split_rows(
            ("Short", dist.pass_locations.short),
            ("Medium", dist.pass_locations.medium),
            ("Long", dist.pass_locations.long),
        ),
        hide_index=True,
        width="stretch",
    )
    st.markdown("**Direction of distribution**")
    d1, d2, d3 = st.columns(3)
    d1.metric(
        "Forward",
        (
            f"{dist.pass_directions.forward.success}/{dist.pass_directions.forward.total}"
            if dist.pass_directions.forward.total
            else directions.forward
        ),
    )
    d2.metric(
        "Sideways",
        (
            f"{dist.pass_directions.sideways.success}/{dist.pass_directions.sideways.total}"
            if dist.pass_directions.sideways.total
            else directions.sideways
        ),
    )
    d3.metric(
        "Backward",
        (
            f"{dist.pass_directions.backward.success}/{dist.pass_directions.backward.total}"
            if dist.pass_directions.backward.total
            else directions.backward
        ),
    )
    st.markdown("**Chance creation**")
    st.dataframe(
        _split_rows(
            ("Crosses", dist.crosses),
            ("Progressive passes", dist.progressive_passes),
            ("Cutbacks", dist.cutbacks),
        ),
        hide_index=True,
        width="stretch",
    )


def render_possession(profile: PlayerMatchProfile) -> None:
    """Possession time, share, recoveries, and balls lost."""

    st.subheader("Possession")
    st.caption("On-ball time plus recoveries and turnovers by third.")
    possession = profile.possession
    p1, p2 = st.columns(2)
    p1.metric("Possession time", f"{possession.time_minutes:.1f} min")
    p2.metric("Possession %", f"{possession.percentage:.1f}%")
    recoveries = profile.defensive.ball_recoveries
    lost = profile.defensive.ball_lost
    st.markdown("**Recoveries / ball lost**")
    st.dataframe(
        [
            {
                "Third": "Defensive",
                "Recoveries": recoveries.defensive_third,
                "Ball lost": lost.defensive_third,
            },
            {
                "Third": "Middle",
                "Recoveries": recoveries.middle_third,
                "Ball lost": lost.middle_third,
            },
            {
                "Third": "Final",
                "Recoveries": recoveries.final_third,
                "Ball lost": lost.final_third,
            },
        ],
        hide_index=True,
        width="stretch",
    )


def render_tactical_pitch(
    actions: tuple[PitchAction, ...],
    *,
    subject: str = "this player",
) -> None:
    """Interactive 2D pitch: selected team's or player's shots and passes."""

    st.subheader("Tactical pitch")
    st.caption(
        "Shot and pass locations on the FIFA 0–100 tagging grid "
        "(touchlines, halfway line, 18-yard boxes). "
        "Tags without valid coordinates are omitted."
    )
    fig, ax = draw_football_pitch()
    plotted, skipped = scatter_match_actions(ax, actions)
    st.pyplot(fig, width="stretch")
    plt.close(fig)
    if plotted == 0:
        st.info(f"No plottable shot or pass locations for {subject}.")
    elif skipped:
        st.caption(
            f"Plotted {plotted} actions. Skipped {skipped} tags with missing "
            "or out-of-range coordinates."
        )
    else:
        st.caption(f"Plotted {plotted} shot and pass locations.")


def render_dashboard(
    load: ProfileLoad,
    *,
    collective: bool = False,
    show_chrome: bool = True,
) -> None:
    """Compose the four-pillar layout and pitch map for one loaded profile."""

    profile = load.profile
    name = profile.player_name or profile.position or "Player"
    if collective or not show_chrome:
        st.markdown(f"#### {name}")
        st.caption(load.message)
    else:
        header_l, header_r = st.columns([3, 1])
        with header_l:
            jersey = f"#{profile.jersey_number} " if profile.jersey_number else ""
            st.markdown("### Player match dashboard")
            st.write(
                f"{jersey}{name}  ·  {profile.position or 'Player'}  ·  "
                f"match `{profile.match_id}`  ·  player `{profile.player_id}`"
            )
        with header_r:
            if load.source == "live":
                st.success("Live FastAPI")
            elif load.source == "collected":
                st.success("Collected match")
            else:
                st.warning("Dummy fallback")
            st.caption(load.message)

    top_l, top_r = st.columns(2)
    with top_l:
        render_offensive(profile)
    with top_r:
        render_defensive(profile)
    bottom_l, bottom_r = st.columns(2)
    with bottom_l:
        render_distribution(profile, load.directions)
    with bottom_r:
        render_possession(profile)
    subject = name if collective else "this player"
    render_tactical_pitch(load.actions, subject=subject)


def _format_elapsed(seconds: float) -> str:
    """Format a loading timer as ``MM:SS`` or ``H:MM:SS``."""

    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_bytes(size: int) -> str:
    if size >= 1024**3:
        return f"{size / (1024 ** 3):.2f} GB"
    if size >= 1024**2:
        return f"{size / (1024 ** 2):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def _eta_label(elapsed: float, fraction: float) -> str:
    if fraction < 0.04:
        return "estimating remaining time…"
    remaining = elapsed * (1.0 - fraction) / max(fraction, 1e-6)
    return f"~{_format_elapsed(remaining)} remaining"


def render_upload_loader() -> tuple[Callable[[str, float], None], Callable[[], None]]:
    """Main-panel loading bar with elapsed time for a film upload."""

    started = time.monotonic()
    st.subheader("Collecting match stats")
    st.caption(
        "Watching every sampled frame of the film on disk. "
        "Keep this tab open — a full match can take hours."
    )
    bar = st.progress(0, text="Preparing the match film…")
    meta = st.empty()
    meta.caption("Elapsed 00:00  ·  estimating remaining time…")

    def update(label: str, fraction: float) -> None:
        elapsed = time.monotonic() - started
        clamped = min(1.0, max(0.0, fraction))
        bar.progress(clamped, text=label)
        meta.caption(f"Elapsed {_format_elapsed(elapsed)}  ·  {_eta_label(elapsed, clamped)}")

    def finish() -> None:
        elapsed = time.monotonic() - started
        bar.progress(1.0, text="Collection complete")
        meta.caption(f"Finished in {_format_elapsed(elapsed)}")

    return update, finish


def render_perspective_banner(
    perspective: SheetPerspective,
    *,
    tag_source: str = "official",
    rundown: MatchRundown | None = None,
) -> None:
    """Explain film vs one-team XML so Home/Away never overclaims the score."""

    if tag_source == "film" and rundown is not None:
        home = rundown.summary.home_team_name or "Home"
        away = rundown.summary.away_team_name or "Away"
        sheets = team_sheets_from_rundown(rundown)
        tagged_h = sheets[0].goals if sheets else 0
        tagged_a = sheets[1].goals if len(sheets) > 1 else 0
        oh = getattr(rundown.summary, "official_home_goals", None)
        oa = getattr(rundown.summary, "official_away_goals", None)
        if oh is not None and oa is not None:
            st.success(
                f"Film Analyse · **{home} {int(oh)}–{int(oa)} {away}** "
                f"(distinti score · tagged goals {tagged_h}–{tagged_a})."
            )
        else:
            st.success(
                f"Film Analyse · **{home} {tagged_h}–{tagged_a} {away}** "
                "(auto-tagged — pin distinti score for the official result)."
            )
        return
    if tag_source == "film":
        st.success(
            f"Film Analyse Stats · both teams auto-tagged · "
            f"{perspective.analysed_team_name} {perspective.analysed_goals}–"
            f"{perspective.opposition_goals_on_sheet} {perspective.opposition_team_name}."
        )
        return
    if not perspective.one_sided:
        st.success(
            f"Official two-team sheet · "
            f"{perspective.analysed_team_name} {perspective.analysed_goals}–"
            f"{perspective.opposition_goals_on_sheet} {perspective.opposition_team_name}"
        )
        return
    st.warning(
        f"**{perspective.analysed_team_name} analysis sheet** — your one-team XML, "
        "not a full Home/Away board. "
        f"Offensive / construction tags are {perspective.analysed_team_name}. "
        f"Defensive tags are still {perspective.analysed_team_name}'s defending "
        f"(saves, recoveries, goals conceded). "
        f"{perspective.opposition_team_name} only appears where this export mentions them "
        "— usually Goal subiti, which can undercount the real score. "
        "For both-team stats without the other side's XML, run Analyse Stats on the film."
    )
    score_l, score_r = st.columns(2)
    score_l.metric(
        f"{perspective.analysed_team_name} goals (this sheet)",
        perspective.analysed_goals,
    )
    score_r.metric(
        f"{perspective.opposition_team_name} goals on this sheet",
        perspective.opposition_goals_on_sheet,
    )
    st.caption(
        f"Score line: {perspective.score_line}. "
        "Pair the other side's analysis XML only if you want an official scoresheet; "
        "otherwise use film Analyse Stats for an automated both-team rundown."
    )


def summary_shots_bogus(rundown: MatchRundown) -> bool:
    return int(getattr(rundown.summary, "shots", 0) or 0) > 30


def render_score_repin(rundown: MatchRundown) -> None:
    """Fix a wrong film scoreboard (e.g. 16–4) without re-watching."""

    from analytics.match_tags import is_placeholder_team_name, prefer_team_name

    tag_source = getattr(rundown.summary, "tag_source", "official") or "official"
    if tag_source != "film":
        return
    sheets = team_sheets_from_rundown(rundown)
    home_name = prefer_team_name(
        rundown.summary.home_team_name,
        sheets[0].team_name if sheets else None,
        str(st.session_state.get("analyse_home_name") or ""),
        fallback="Home",
    )
    away_name = prefer_team_name(
        rundown.summary.away_team_name,
        sheets[1].team_name if len(sheets) > 1 else None,
        str(st.session_state.get("analyse_away_name") or ""),
        fallback="Away",
    )
    official_h = getattr(rundown.summary, "official_home_goals", None)
    official_a = getattr(rundown.summary, "official_away_goals", None)
    if official_h is not None and official_a is not None:
        cur_home, cur_away = int(official_h), int(official_a)
    elif sheets:
        cur_home = sheets[0].goals
        cur_away = sheets[1].goals if len(sheets) > 1 else 0
    else:
        cur_home, cur_away = 0, 0
    # Prefill from Home Analyse widgets when the sheet still says Home/Away 0–3.
    ui_h = st.session_state.get("analyse_official_home_goals")
    ui_a = st.session_state.get("analyse_official_away_goals")
    if (
        official_h is None
        and isinstance(ui_h, (int, float))
        and isinstance(ui_a, (int, float))
        and (int(ui_h), int(ui_a)) != (0, 0)
    ):
        cur_home, cur_away = int(ui_h), int(ui_a)
    bogus = cur_home + cur_away > 8 or summary_shots_bogus(rundown)
    names_wrong = is_placeholder_team_name(
        rundown.summary.home_team_name
    ) or is_placeholder_team_name(rundown.summary.away_team_name)
    with st.expander(
        "Fix film tags (shots / score / names)",
        expanded=bogus or names_wrong or official_h is None,
    ):
        st.caption(
            "Film CV invents box traffic as shots and goals. "
            "Pin the distinti score and club names — no re-analyse needed."
        )
        if st.button(
            "Clean fake shots & goals",
            use_container_width=True,
            key="clean_film_noise",
        ):
            try:
                from analytics.distinti import clean_film_rundown

                updated = clean_film_rundown(rundown)
                st.session_state[RUNDOWN_KEY] = rundown_to_json(updated)
                history_msg = _remember_collection(updated)
                st.session_state[PERSIST_KEY] = f"Film tags cleaned · {history_msg}"
                st.success(
                    f"Cleaned · {updated.summary.shots} shots · "
                    f"{updated.summary.goals} goals"
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        name_cols = st.columns(2)
        with name_cols[0]:
            fix_home_name = st.text_input(
                "Home team name",
                value=home_name,
                key="repin_home_name",
            ).strip()
        with name_cols[1]:
            fix_away_name = st.text_input(
                "Away team name",
                value=away_name,
                key="repin_away_name",
            ).strip()
        cols = st.columns(2)
        with cols[0]:
            fix_home = st.number_input(
                f"{fix_home_name or home_name} goals",
                min_value=0,
                max_value=30,
                value=min(max(cur_home, 0), 30),
                step=1,
                key="repin_home_goals",
            )
        with cols[1]:
            fix_away = st.number_input(
                f"{fix_away_name or away_name} goals",
                min_value=0,
                max_value=30,
                value=min(max(cur_away, 0), 30),
                step=1,
                key="repin_away_goals",
            )
        if st.button("Apply official score", type="primary", use_container_width=True):
            try:
                from analytics.distinti import pin_rundown_score

                home_g, away_g = int(fix_home), int(fix_away)
                updated = pin_rundown_score(
                    rundown,
                    home_goals=home_g,
                    away_goals=away_g,
                    home_team_name=fix_home_name or home_name,
                    away_team_name=fix_away_name or away_name,
                )
                st.session_state[RUNDOWN_KEY] = rundown_to_json(updated)
                history_msg = _remember_collection(updated)
                label_h = updated.summary.home_team_name
                label_a = updated.summary.away_team_name
                st.session_state[PERSIST_KEY] = (
                    f"Score pinned to {label_h} {home_g}–{away_g} {label_a} · {history_msg}"
                )
                st.success(f"Scoreboard set to {label_h} {home_g}–{away_g} {label_a}.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if st.button(
            "Re-analyse film from scratch",
            use_container_width=True,
            key="reanalyse_film_fresh",
        ):
            try:
                source = latest_ready_film()
                if source is None:
                    raise ValueError("No match film in the inbox to re-analyse.")
                status_path = start_collect_job(
                    source,
                    home_kit_hex=str(st.session_state.get("last_home_kit") or "") or None,
                    away_kit_hex=str(st.session_state.get("last_away_kit") or "") or None,
                    home_team_name=str(st.session_state.get("last_home_name") or "") or None,
                    away_team_name=str(st.session_state.get("last_away_name") or "") or None,
                    lineup_json=str(st.session_state.get("last_lineup_json") or "") or None,
                    force_fresh=True,
                )
                st.session_state[JOB_KEY] = str(status_path)
                st.session_state.pop(RUNDOWN_KEY, None)
                st.session_state.pop("analyse_cleared", None)
                st.session_state[PERSIST_KEY] = (
                    "Fresh re-analyse queued (old tags cleared). "
                    f"Status: {status_path.name}"
                )
                _request_nav("Analysing")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def render_match_summary(rundown: MatchRundown) -> None:
    """Headline match totals collected from the uploaded event feed."""

    summary = rundown.summary
    perspective = analysis_perspective(rundown)
    tag_source = getattr(rundown.summary, "tag_source", "official") or "official"
    st.subheader("Match rundown")
    render_perspective_banner(perspective, tag_source=tag_source, rundown=rundown)
    render_score_repin(rundown)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Events", summary.event_count)
    c2.metric("Players", summary.player_count)
    oh = getattr(summary, "official_home_goals", None)
    oa = getattr(summary, "official_away_goals", None)
    if oh is not None and oa is not None:
        c3.metric("Score (distinti)", f"{int(oh)}–{int(oa)}")
    elif perspective.one_sided:
        c3.metric(
            f"{perspective.analysed_team_name} goals",
            perspective.analysed_goals,
        )
    else:
        sheets = team_sheets_from_rundown(rundown)
        if len(sheets) >= 2:
            c3.metric("Score", f"{sheets[0].goals}–{sheets[1].goals}")
        else:
            c3.metric("Goals", summary.goals)
    c4.metric("Shots", summary.shots)
    c5.metric("Passes", summary.passes)
    st.caption(
        f"Match `{summary.match_id}`  ·  {summary.duration_minutes:.1f} minutes of collected play. "
        "Every number below is counted from the match tags (passes, shots, "
        "recoveries) — the same sheet Spiideo / Wyscout XML export uses."
    )
    render_tag_inventory(rundown, perspective=perspective)
    render_team_sheet(rundown, perspective=perspective)
    render_match_tags(rundown)
    render_highlight_moments(rundown)


def render_tag_inventory(
    rundown: MatchRundown,
    *,
    perspective: SheetPerspective | None = None,
) -> None:
    """Show how many of each action the machine (or official XML) already tagged."""

    view = perspective or analysis_perspective(rundown)
    rows = tag_inventory_rows(
        rundown,
        analysed_team_id=view.analysed_team_id,
        analysed_label=view.analysed_team_name,
        opposition_label=view.opposition_team_name,
        one_sided=view.one_sided,
    )
    if not rows:
        return
    st.subheader("What you no longer have to tag")
    if view.one_sided:
        st.caption(
            f"One analysis sheet for {view.analysed_team_name}. "
            f"The {view.opposition_team_name} column is only tags that appear on "
            "this export (usually goals conceded) — not their full offensive sheet."
        )
    else:
        st.caption(
            "Each row is one action type an analyst would otherwise mark by hand. "
            "Totals are the collected sheet — download CSV or XML below to take "
            "this into the rest of the data-collection workflow."
        )
    st.dataframe(rows, hide_index=True, width="stretch")


def render_team_sheet(
    rundown: MatchRundown,
    *,
    perspective: SheetPerspective | None = None,
) -> None:
    """Impact-style 15-stat board — analysed side only when the sheet is one-team."""

    view = perspective or analysis_perspective(rundown)
    sheets = team_sheets_from_rundown(rundown)
    if not sheets:
        return
    if view.one_sided and view.analysed_team_id is not None:
        sheets = [sheet for sheet in sheets if sheet.team_id == view.analysed_team_id] or sheets[:1]
        st.subheader(f"{view.analysed_team_name} team statistics")
        st.caption(
            f"15 basic Impact-style stats for the analysed side only. "
            f"{view.opposition_team_name} does not get a parallel board from this "
            "one-team XML — pair their analysis file for a full two-team sheet."
        )
    else:
        st.subheader("Team statistics")
        st.caption(
            "The 15 basic team stats Impact-style analysis publishes: counted "
            "from this match's tags, not a second spreadsheet."
        )
    st.dataframe(team_sheet_rows(sheets), hide_index=True, width="stretch")


def render_highlight_moments(rundown: MatchRundown) -> None:
    """15-second windows around goals, shots, saves, and corners."""

    moments = highlight_moments_from_rundown(rundown)
    if not moments:
        return
    st.subheader("15-second moments")
    st.caption(
        "Each row is a 15-second clip window around a tagged highlight "
        "(goal, shot, save, corner). Same clock as the tag sheet."
    )
    rows = [
        {
            "Clock": moment.clock,
            "Moment": moment.kind,
            "Player": moment.player,
            "Team": moment.team_name,
            "Window ms": f"{moment.start_ms}–{moment.end_ms}",
        }
        for moment in moments[:80]
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
    if len(moments) > 80:
        st.caption(f"Showing the first 80 of {len(moments)} highlight windows.")


def render_match_tags(rundown: MatchRundown) -> None:
    """Show the tag sheet the rundown was counted from, plus XML download."""

    st.subheader("Match tags")
    st.caption(
        "Each row is one tagged action. Player pillars are the sums of these "
        "tags. Download XML for a tagger re-import, or CSV for a spreadsheet."
    )
    attacks = attacks_from_events(rundown.events)
    a1, a2 = st.columns(2)
    a1.metric("Attack sequences", len(attacks))
    a2.metric(
        "Attacks ending in a shot/goal",
        sum(1 for attack in attacks if attack["end_type"] in {"shot", "goal"}),
    )
    names = {profile.player_id: profile_label(profile) for profile in rundown.players}
    rows = []
    preview = rundown.events[:500]
    for event in preview:
        actor = names.get(event.player_id, "—") if event.player_id else "—"
        rows.append(
            {
                "Clock": f"{event.period}' {event.minute:02d}:{event.second:02d}",
                "Tag": event.event_type.value,
                "Player": actor,
                "X": round(event.x, 1),
                "Y": round(event.y, 1),
                "End X": None if event.end_x is None else round(event.end_x, 1),
                "Goal": event.is_goal,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
    if len(rundown.events) > 500:
        st.caption(f"Showing the first 500 of {len(rundown.events)} tags.")
    pair = export_both_oncesport_xml(rundown)
    home_name = rundown.summary.home_team_name or "Home"
    away_name = rundown.summary.away_team_name or "Away"
    xml_home, xml_away, xml_all, csv_col = st.columns(4)
    with xml_home:
        st.download_button(
            f"Download {home_name} OnceSport XML",
            data=pair["home"],
            file_name=f"{home_name}_Home.xml",
            mime="application/xml",
            use_container_width=True,
            key="dl_oncesport_home",
        )
    with xml_away:
        st.download_button(
            f"Download {away_name} OnceSport XML",
            data=pair["away"],
            file_name=f"{away_name}_Away.xml",
            mime="application/xml",
            use_container_width=True,
            key="dl_oncesport_away",
        )
    with xml_all:
        st.download_button(
            "Download match tags (XML)",
            data=rundown_to_xml(rundown),
            file_name="match_tags.xml",
            mime="application/xml",
            use_container_width=True,
            key="dl_match_tags",
        )
    with csv_col:
        st.download_button(
            "Download match tags (CSV)",
            data=rundown_to_csv(rundown),
            file_name="match_tags.csv",
            mime="text/csv",
            use_container_width=True,
            key="dl_match_csv",
        )


RUNDOWN_KEY = "collected_rundown"
PERSIST_KEY = "ingest_persist_message"
JOB_KEY = "collect_job_path"
NAV_KEY = "app_nav"
PENDING_NAV_KEY = "pending_app_nav"


def _request_nav(section: str) -> None:
    """Queue a tab change; applied before the nav radio is created next run."""

    st.session_state[PENDING_NAV_KEY] = section


def _go_home() -> None:
    _request_nav("Home")


def _go_match() -> None:
    _request_nav("Match rundown")


def _go_history() -> None:
    _request_nav("History")


def _remember_collection(rundown: MatchRundown) -> str:
    """Auto-save every finished collect into Analyse history."""

    entry = save_rundown_to_history(rundown)
    return f"Saved to History · {entry.label}"


def _pdf_filename(label: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in label)
    return f"statman_{safe[:80] or 'match'}.pdf"


def render_pdf_download(rundown: MatchRundown, *, key: str, label: str | None = None) -> None:
    """Download button for the full match statistics PDF."""

    home = rundown.summary.home_team_name or "Home"
    away = rundown.summary.away_team_name or "Away"
    report_label = label or f"{home}_vs_{away}"
    try:
        pdf_bytes = build_match_report_pdf(rundown)
    except Exception as exc:  # noqa: BLE001 — keep the match page usable if PDF fails
        st.caption(f"PDF report unavailable ({exc}).")
        return
    st.download_button(
        "Download full report (PDF)",
        data=pdf_bytes,
        file_name=_pdf_filename(report_label),
        mime="application/pdf",
        use_container_width=True,
        key=key,
    )


def render_app_nav(*, has_match: bool, analysing: bool) -> str:
    """Top Home / Match / History tabs."""

    options = ["Home", "History"]
    if has_match:
        options.insert(1, "Match rundown")
    if analysing:
        options.append("Analysing")
    pending = st.session_state.pop(PENDING_NAV_KEY, None)
    if isinstance(pending, str) and pending in options:
        st.session_state[NAV_KEY] = pending
    current = str(st.session_state.get(NAV_KEY, "") or "")
    if current not in options:
        if analysing and not has_match:
            st.session_state[NAV_KEY] = "Analysing"
        elif has_match:
            st.session_state[NAV_KEY] = "Match rundown"
        else:
            st.session_state[NAV_KEY] = "Home"
    return st.radio(
        "Section",
        options=options,
        horizontal=True,
        key=NAV_KEY,
        label_visibility="collapsed",
    )


def _hydrate_collect_job() -> None:
    """Resume a background analyse after a refresh or a new phone session."""

    if st.session_state.get(RUNDOWN_KEY):
        return
    job_path_raw = str(st.session_state.get(JOB_KEY, "") or "")
    if not job_path_raw and not st.session_state.get("analyse_cleared"):
        latest = latest_job_status_path()
        if latest is not None:
            job_path_raw = str(latest)
            st.session_state[JOB_KEY] = job_path_raw
    if not job_path_raw:
        return
    status = read_job_status(Path(job_path_raw))
    if status is None:
        return
    if status.get("state") == "done":
        loaded = load_job_rundown(status)
        if loaded is not None:
            st.session_state[RUNDOWN_KEY] = rundown_to_json(loaded)
            history_msg = _remember_collection(loaded)
            st.session_state[PERSIST_KEY] = (
                f"{status.get('label') or 'Background collect ready.'} · {history_msg}"
            )
            if st.session_state.get(NAV_KEY) not in {"Home", "Match rundown", "History"}:
                if st.session_state.get(PENDING_NAV_KEY) not in {
                    "Home",
                    "Match rundown",
                    "History",
                }:
                    _go_match()
        return
    if status.get("state") == "error":
        st.session_state[PERSIST_KEY] = str(status.get("error") or "Background collect failed.")


def _stored_rundown() -> MatchRundown | None:
    stored = st.session_state.get(RUNDOWN_KEY)
    if not stored:
        return None
    return rundown_from_mapping(stored)


def _load_sample_match(base_url: str) -> str | None:
    """Collect the bundled sample into session state. Returns an error or None."""

    try:
        rundown = collect_sample_match()
        st.session_state[RUNDOWN_KEY] = rundown_to_json(rundown)
        st.session_state.pop("analyse_cleared", None)
        _persisted, message = asyncio.run(persist_rundown(base_url, rundown))
        history_msg = _remember_collection(rundown)
        st.session_state[PERSIST_KEY] = f"{message} · {history_msg}"
        _go_match()
    except ValueError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 — surface unexpected collect failures
        return f"Sample collection failed ({exc})."
    return None


def render_sidebar() -> str:
    """Settings, sample load, and optional FastAPI demo — analyse lives on the page."""

    inbox_dir = film_inbox_dir()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    film_upload_dir().mkdir(parents=True, exist_ok=True)

    st.sidebar.header("Settings")
    base_url = st.sidebar.text_input("FastAPI base URL", value=DEFAULT_BASE_URL).strip()
    if not base_url:
        base_url = DEFAULT_BASE_URL
    collect_sample = st.sidebar.button(
        "Load sample match",
        use_container_width=True,
        key="sidebar_sample",
    )
    clear_collected = st.sidebar.button("Clear collected match", use_container_width=True)
    with st.sidebar.expander("Official JSON or XML"):
        uploaded_json = st.file_uploader(
            "AutoData JSON or Wyscout / Nacsport XML",
            type=["json", "xml"],
            help="Official event tags. Wyscout analysis XML is collected as the rundown.",
        )
        collect_json = st.button("Collect tagged file", use_container_width=True)

    if clear_collected:
        st.session_state.pop(RUNDOWN_KEY, None)
        st.session_state.pop(PERSIST_KEY, None)
        st.session_state.pop(JOB_KEY, None)
        st.session_state["analyse_cleared"] = True
        _go_home()

    error: str | None = None
    if collect_sample:
        error = _load_sample_match(base_url)
        if error is None:
            _go_match()
    elif collect_json:
        if uploaded_json is None:
            error = "Choose a JSON or XML tag file first, or use Analyse Stats on the main page."
        else:
            try:
                rundown = collect_uploaded_bytes(uploaded_json.getvalue())
                st.session_state[RUNDOWN_KEY] = rundown_to_json(rundown)
                _persisted, message = asyncio.run(persist_rundown(base_url, rundown))
                history_msg = _remember_collection(rundown)
                st.session_state[PERSIST_KEY] = f"{message} · {history_msg}"
                _go_match()
            except ValueError as exc:
                error = str(exc)
    if error:
        st.sidebar.error(error)

    rundown = _stored_rundown()
    if rundown is not None:
        st.sidebar.success(
            f"Collected {rundown.summary.event_count} events · " f"{rundown.summary.goals} goals"
        )
        persist_message = st.session_state.get(PERSIST_KEY)
        if persist_message:
            st.sidebar.caption(str(persist_message))
    return base_url


def render_job_progress(status: dict[str, object]) -> None:
    """Main-panel watch for a hours-long background analyse."""

    st.subheader("Analysing match")
    fraction = float(status.get("fraction") or 0.0)
    label = str(status.get("label") or "Watching the film…")
    state = str(status.get("state") or "").strip().lower()
    st.progress(min(1.0, max(0.0, fraction)), text=label)
    st.caption(f"{fraction * 100:.0f}%  ·  {label}")
    film = str(status.get("film") or "")
    if film:
        st.caption(f"Film: `{Path(film).name}`")
    updated = str(status.get("updated_at") or status.get("started_at") or "")
    if updated:
        st.caption(f"Last progress update: `{updated}`")
    if state in {"running", "claimed"} and updated:
        try:
            from analytics.collect_worker import job_age_seconds, stale_progress_seconds

            age = job_age_seconds(status)
            if age >= stale_progress_seconds():
                st.warning(
                    f"No progress for {int(age // 60)} minutes — the worker looks stuck. "
                    "Redeploy **Web** (or wait for the watchdog) so the job is re-queued."
                )
        except Exception:  # noqa: BLE001 — UI must not crash on age parse
            pass
    if state == "queued":
        updated = str(status.get("updated_at") or status.get("started_at") or "")
        jobs_dir = str(status.get("jobs_dir") or "")
        st.info(
            "Queued for Analyse. On Railway the Web service runs an **embedded worker** "
            "that should claim this within a few seconds. "
            f"Queued since: `{updated or 'unknown'}`. "
            + (f"Job folder: `{jobs_dir}`." if jobs_dir else "")
        )
        st.caption(
            "Web logs should show `embedded Analyse worker` and "
            "`[statman-worker] starting ….status.json`. "
            "A separate Worker service is optional."
        )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _same_origin_upload() -> bool:
    return _env_flag("ENJOYSTATS_SAME_ORIGIN_UPLOAD")


def _use_streamlit_film_upload() -> bool:
    """Use Streamlit's uploader only when explicitly enabled.

    On Railway the same-origin portal serves chunked ``/api/.../film/chunk``
    uploads. Streamlit's built-in uploader often returns AxiosError 502 there.
    """

    if _same_origin_upload():
        return False
    if _env_flag("STATMAN_STREAMLIT_FILM_UPLOAD"):
        return True
    return False


def _ensure_api_watchdog() -> None:
    """Background loop so Uvicorn is restarted if it dies mid-session."""

    if _use_streamlit_film_upload():
        return
    if st.session_state.get("_api_watchdog_started"):
        return
    st.session_state["_api_watchdog_started"] = True

    def _loop() -> None:
        while True:
            try:
                ensure_api_running(wait_s=12.0)
            except Exception:  # noqa: BLE001 — never kill the UI thread via watchdog
                pass
            time.sleep(12)

    threading.Thread(target=_loop, name="enjoystats-api-watchdog", daemon=True).start()


def render_streamlit_film_uploader() -> None:
    """Save a match film into the shared /data inbox (Railway-safe)."""

    st.subheader("Match film")
    uploaded = st.file_uploader(
        "Upload match film",
        type=[suffix.lstrip(".") for suffix in VIDEO_SUFFIXES],
        key="analyse_streamlit_film",
    )
    if uploaded is None:
        return
    if st.button(
        "Save film to inbox", type="primary", use_container_width=True, key="save_st_film"
    ):
        try:
            name = safe_film_name(getattr(uploaded, "name", "") or "match.mp4")
            destination = film_inbox_dir() / name
            progress = st.progress(0, text="Saving film…")

            def _on_progress(done: int, total: int) -> None:
                if total > 0:
                    progress.progress(min(done / total, 1.0), text=f"Saving… {_format_bytes(done)}")

            saved = save_uploaded_film(uploaded, destination, on_progress=_on_progress)
            progress.progress(1.0, text="Saved")
            st.success(f"Saved: **{saved.name}** · {_format_bytes(saved.stat().st_size)}")
            st.rerun()
        except (ValueError, OSError, VideoCollectError) as exc:
            st.error(str(exc))


def render_film_uploader_panel(base_url: str) -> None:
    """Match-film upload: Streamlit on Railway, chunked FastAPI locally/portal."""

    if _use_streamlit_film_upload():
        render_streamlit_film_uploader()
        return

    _ensure_api_watchdog()
    status = ensure_api_running(wait_s=20.0)
    same_origin = _same_origin_upload()
    # Same-origin (portal / try-link): browser posts to this page's host.
    # Otherwise post straight to the local FastAPI origin we just ensured.
    api_for_browser = "" if same_origin else "http://127.0.0.1:8000"
    upload_url = "/upload-film" if same_origin else f"{api_for_browser}/upload-film"
    local_ok = status.get("ok") or api_is_healthy("http://127.0.0.1:8000")
    remote_ok = True
    if not same_origin:
        remote_ok = asyncio.run(probe_api(base_url.rstrip("/")))
    if not local_ok:
        st.error(
            "Upload API would not start. "
            f"{status.get('message') or 'Check .local-run/uvicorn.log'}."
        )
        return
    if (
        not same_origin
        and not remote_ok
        and base_url.rstrip("/")
        not in {
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        }
    ):
        st.warning(
            "Sidebar FastAPI URL is unreachable from this machine. "
            "Uploads still go to the always-on local API on :8000."
        )
    st.subheader("Match film")
    if status.get("started"):
        st.success("Upload API was down — started it automatically.")
    st.link_button("Open uploader full-screen", upload_url)
    import streamlit.components.v1 as components

    components.html(upload_page_html(api_for_browser), height=320, scrolling=False)


def _sync_analyse_identity_from_uploads(
    lineup_file: object | None,
    distinti_file: object | None,
    latest_film: object | None,
) -> None:
    """Write club names + official score into session_state before widgets render."""

    import hashlib

    from analytics.match_tags import infer_team_names, is_placeholder_team_name

    if latest_film is not None:
        inferred_h, inferred_a = infer_team_names(getattr(latest_film, "name", ""))
        if not is_placeholder_team_name(inferred_h) and is_placeholder_team_name(
            str(st.session_state.get("analyse_home_name") or "Home")
        ):
            st.session_state["analyse_home_name"] = inferred_h
        if not is_placeholder_team_name(inferred_a) and is_placeholder_team_name(
            str(st.session_state.get("analyse_away_name") or "Away")
        ):
            st.session_state["analyse_away_name"] = inferred_a

    if lineup_file is not None:
        raw = lineup_file.getvalue()
        sig = hashlib.sha1(raw).hexdigest()
        if st.session_state.get("_analyse_lineup_sig") != sig:
            st.session_state["_analyse_lineup_sig"] = sig
            try:
                parsed = parse_lineup_bytes(
                    raw,
                    filename=getattr(lineup_file, "name", ""),
                    home_team=str(st.session_state.get("analyse_home_name") or "Home"),
                    away_team=str(st.session_state.get("analyse_away_name") or "Away"),
                )
                if not is_placeholder_team_name(parsed.home_team):
                    st.session_state["analyse_home_name"] = parsed.home_team
                if not is_placeholder_team_name(parsed.away_team):
                    st.session_state["analyse_away_name"] = parsed.away_team
            except ValueError:
                pass

    if distinti_file is not None:
        raw = distinti_file.getvalue()
        sig = hashlib.sha1(raw).hexdigest()
        if st.session_state.get("_analyse_distinti_sig") != sig:
            st.session_state["_analyse_distinti_sig"] = sig
            try:
                facts = parse_distinti_pdf(
                    raw,
                    filename=getattr(distinti_file, "name", "distinti.pdf"),
                )
                if not is_placeholder_team_name(facts.home_team):
                    st.session_state["analyse_home_name"] = facts.home_team
                if not is_placeholder_team_name(facts.away_team):
                    st.session_state["analyse_away_name"] = facts.away_team
                if facts.home_goals is not None and facts.away_goals is not None:
                    st.session_state["analyse_official_home_goals"] = int(facts.home_goals)
                    st.session_state["analyse_official_away_goals"] = int(facts.away_goals)
            except ValueError:
                pass


def render_analyse_landing(base_url: str) -> None:
    """Home: match film + line-up CSV + distinti PDF, then Analyse."""

    import json as _json

    from analytics.match_tags import prefer_team_name

    render_film_uploader_panel(base_url)

    st.subheader("Line-up CSV")
    st.download_button(
        "Download sample line-up CSV",
        data=example_lineup_csv(),
        file_name="lineup_sample.csv",
        mime="text/csv",
        use_container_width=True,
        key="dl_lineup_sample",
    )
    lineup_file = st.file_uploader(
        "Upload Home+Away line-up (CSV or JSON)",
        type=["csv", "json"],
        key="analyse_lineup_file",
    )

    st.subheader("Distinti PDF")
    distinti_file = st.file_uploader(
        "Upload distinti / tabellino (PDF)",
        type=["pdf"],
        key="analyse_distinti_pdf",
    )

    latest_film = latest_ready_film()
    _sync_analyse_identity_from_uploads(lineup_file, distinti_file, latest_film)

    name_cols = st.columns(2)
    with name_cols[0]:
        home_team_name = st.text_input("Home team", key="analyse_home_name").strip()
    with name_cols[1]:
        away_team_name = st.text_input("Away team", key="analyse_away_name").strip()
    kit_cols = st.columns(2)
    with kit_cols[0]:
        home_kit_hex = st.color_picker("Home kit colour", value="#1e3a8a", key="analyse_home_kit")
    with kit_cols[1]:
        away_kit_hex = st.color_picker("Away kit colour", value="#dc2626", key="analyse_away_kit")

    if "analyse_official_home_goals" not in st.session_state:
        st.session_state["analyse_official_home_goals"] = 0
    if "analyse_official_away_goals" not in st.session_state:
        st.session_state["analyse_official_away_goals"] = 0
    score_cols = st.columns(2)
    with score_cols[0]:
        manual_home_goals = st.number_input(
            "Official home goals",
            min_value=0,
            max_value=30,
            step=1,
            key="analyse_official_home_goals",
        )
    with score_cols[1]:
        manual_away_goals = st.number_input(
            "Official away goals",
            min_value=0,
            max_value=30,
            step=1,
            key="analyse_official_away_goals",
        )
    use_manual_score = st.checkbox(
        "Pin goals to official scoreline (required for a real result)",
        value=True,
        key="analyse_pin_official_score",
    )

    lineup_json_text = ""
    payload: dict[str, object] = {
        "home_team": prefer_team_name(home_team_name, fallback="Home"),
        "away_team": prefer_team_name(away_team_name, fallback="Away"),
        "home": [],
        "away": [],
    }
    if lineup_file is not None:
        try:
            parsed = parse_lineup_bytes(
                lineup_file.getvalue(),
                filename=getattr(lineup_file, "name", ""),
                home_team=home_team_name or "Home",
                away_team=away_team_name or "Away",
            )
            payload = {
                "home_team": prefer_team_name(
                    home_team_name, parsed.home_team, fallback="Home"
                ),
                "away_team": prefer_team_name(
                    away_team_name, parsed.away_team, fallback="Away"
                ),
                "home": [
                    {
                        "jersey": p.jersey,
                        "name": p.name,
                        "position": p.position,
                    }
                    for p in parsed.home
                ],
                "away": [
                    {
                        "jersey": p.jersey,
                        "name": p.name,
                        "position": p.position,
                    }
                    for p in parsed.away
                ],
            }
            st.success(
                f"Line-up loaded · Home {len(parsed.home)} · Away {len(parsed.away)}"
            )
        except ValueError as exc:
            st.error(str(exc))
    if distinti_file is not None:
        try:
            facts = parse_distinti_pdf(
                distinti_file.getvalue(),
                filename=getattr(distinti_file, "name", "distinti.pdf"),
            )
            payload["match_facts"] = facts_to_payload(facts)
            payload["home_team"] = prefer_team_name(
                facts.home_team,
                home_team_name,
                str(payload.get("home_team") or ""),
                fallback="Home",
            )
            payload["away_team"] = prefer_team_name(
                facts.away_team,
                away_team_name,
                str(payload.get("away_team") or ""),
                fallback="Away",
            )
            home_team_name = str(payload["home_team"])
            away_team_name = str(payload["away_team"])
            if not payload.get("home") and facts.home:
                payload["home"] = [
                    {
                        "jersey": p.jersey,
                        "name": p.name,
                        "position": p.position,
                    }
                    for p in facts.home
                ]
            if not payload.get("away") and facts.away:
                payload["away"] = [
                    {
                        "jersey": p.jersey,
                        "name": p.name,
                        "position": p.position,
                    }
                    for p in facts.away
                ]
            st.success(
                f"Distinti loaded · {payload['home_team']} {facts.score_label()} "
                f"{payload['away_team']} · "
                f"players Home {len(facts.home)} / Away {len(facts.away)}"
            )
        except ValueError as exc:
            st.error(str(exc))
    pdf_score = payload.get("match_facts") if isinstance(payload.get("match_facts"), dict) else None
    pdf_has_score = (
        isinstance(pdf_score, dict)
        and pdf_score.get("home_goals") is not None
        and pdf_score.get("away_goals") is not None
    )
    # Pin when checkbox is on or the PDF already has a score.
    # Never let unsynced 0–0 widgets wipe a real distinti scoreline.
    if use_manual_score or pdf_has_score:
        facts_payload = dict(pdf_score or {})
        widget_h, widget_a = int(manual_home_goals), int(manual_away_goals)
        pdf_h = int(pdf_score["home_goals"]) if pdf_has_score else None
        pdf_a = int(pdf_score["away_goals"]) if pdf_has_score else None
        if pdf_has_score and widget_h == 0 and widget_a == 0 and (pdf_h, pdf_a) != (0, 0):
            facts_payload["home_goals"] = pdf_h
            facts_payload["away_goals"] = pdf_a
        elif use_manual_score or not pdf_has_score:
            facts_payload["home_goals"] = widget_h
            facts_payload["away_goals"] = widget_a
            facts_payload["source"] = facts_payload.get("source") or "manual_distinti_score"
        facts_payload["home_team"] = prefer_team_name(
            home_team_name,
            str(facts_payload.get("home_team") or ""),
            str(payload.get("home_team") or ""),
            fallback="Home",
        )
        facts_payload["away_team"] = prefer_team_name(
            away_team_name,
            str(facts_payload.get("away_team") or ""),
            str(payload.get("away_team") or ""),
            fallback="Away",
        )
        payload["home_team"] = facts_payload["home_team"]
        payload["away_team"] = facts_payload["away_team"]
        home_team_name = str(payload["home_team"])
        away_team_name = str(payload["away_team"])
        payload["match_facts"] = facts_payload
        pinned_h = int(facts_payload["home_goals"])
        pinned_a = int(facts_payload["away_goals"])
        st.info(
            f"**{home_team_name} {pinned_h}–{pinned_a} {away_team_name}** "
            "(goals pinned for Analyse)"
        )
        if use_manual_score and pinned_h == 0 and pinned_a == 0 and not pdf_has_score:
            st.warning(
                "Score is pinned at 0–0. Enter the real distinti result "
                "(e.g. 0 and 4) or upload the PDF before Analyse."
            )
    if payload.get("home") or payload.get("away") or payload.get("match_facts"):
        lineup_json_text = _json.dumps(payload)

    films = ready_match_films()
    latest = films[0] if films else None
    if latest is not None:
        st.success(
            f"Ready: **{latest.name}** · {_format_bytes(latest.stat().st_size)}"
            + (f" · {len(films)} films in inbox" if len(films) > 1 else "")
        )

    force_fresh = st.checkbox(
        "Re-watch from scratch (clear old tags — use this for a new rundown)",
        value=bool(st.session_state.get(RUNDOWN_KEY)),
        key="analyse_force_fresh",
    )
    analyse = st.button(
        "Analyse Stats",
        use_container_width=True,
        type="primary",
        key="analyse_film_primary",
    )

    inbox_films = list_inbox_films()
    if len(inbox_films) > 1:
        with st.expander(f"Game-week batch · {len(inbox_films)} films in inbox", expanded=False):
            batch = st.button(
                f"Collect ALL inbox films ({len(inbox_films)})",
                use_container_width=True,
                key="analyse_batch_inbox",
            )
            if batch:
                try:
                    batch_path = start_batch_collect(
                        inbox_films,
                        home_kit_hex=home_kit_hex,
                        away_kit_hex=away_kit_hex,
                        home_team_name=home_team_name or None,
                        away_team_name=away_team_name or None,
                        lineup_json=lineup_json_text or None,
                    )
                    st.session_state["batch_status_path"] = str(batch_path)
                    st.session_state[PERSIST_KEY] = (
                        f"Queued {len(inbox_films)} films. "
                        f"Batch: {batch_path.name}"
                    )
                    _request_nav("Analysing")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    if st.session_state.get("batch_status_path"):
        from pathlib import Path as _Path
        from analytics.batch_collect import read_batch_status

        batch_doc = read_batch_status(_Path(str(st.session_state["batch_status_path"])))
        if batch_doc:
            summary = summarize_batch(batch_doc)
            st.info(
                f"Batch {summary['state']}: {summary['done']}/{summary['total']} done · "
                f"{summary['running']} running · {summary['error']} errors"
            )

    if not analyse:
        return
    try:
        source = latest_ready_film()
        if source is None:
            raise ValueError(
                "Upload the match film above, then click Analyse Stats."
            )
        st.session_state["last_home_kit"] = home_kit_hex
        st.session_state["last_away_kit"] = away_kit_hex
        st.session_state["last_home_name"] = home_team_name or "Home"
        st.session_state["last_away_name"] = away_team_name or "Away"
        st.session_state["last_lineup_json"] = lineup_json_text or ""
        update, finish = render_upload_loader()
        update("Preparing the match…", 0.04)
        if film_has_official_tags(source):
            update("Collecting official tags…", 0.36)

            def _on_collect(label: str, fraction: float) -> None:
                update(label, 0.36 + 0.64 * fraction)

            rundown = collect_from_film_path(
                str(source),
                on_progress=_on_collect,
                home_kit_hex=home_kit_hex,
                away_kit_hex=away_kit_hex,
                home_team_name=home_team_name or None,
                away_team_name=away_team_name or None,
                lineup_json=lineup_json_text or None,
                force_fresh=force_fresh,
            )
            finish()
            st.session_state[RUNDOWN_KEY] = rundown_to_json(rundown)
            st.session_state.pop("analyse_cleared", None)
            _persisted, message = asyncio.run(persist_rundown(base_url, rundown))
            history_msg = _remember_collection(rundown)
            st.session_state[PERSIST_KEY] = f"{message} · {history_msg}"
            _go_match()
            st.rerun()
        else:
            update("Starting background analyse…", 0.2)
            status_path = start_collect_job(
                source,
                home_kit_hex=home_kit_hex,
                away_kit_hex=away_kit_hex,
                home_team_name=home_team_name or None,
                away_team_name=away_team_name or None,
                lineup_json=lineup_json_text or None,
                force_fresh=force_fresh,
            )
            st.session_state[JOB_KEY] = str(status_path)
            st.session_state.pop(RUNDOWN_KEY, None)
            st.session_state.pop("analyse_cleared", None)
            st.session_state[PERSIST_KEY] = (
                "Analyse is running in the background. "
                f"Status: {status_path.name}"
            )
            _request_nav("Analysing")
            finish()
            st.rerun()
    except VideoCollectError as exc:
        st.error(str(exc))
    except ValueError as exc:
        st.error(str(exc))
    except OSError as exc:
        st.error(f"Could not read the match film ({exc}).")
    except Exception as exc:  # noqa: BLE001 — uploader failures are not always ValueError
        st.error(f"{exc}. {UPLOAD_DISCONNECT_HINT}")


def _individual_player_rows(rundown: MatchRundown) -> list[dict[str, object]]:
    """Compact roster table: minutes, goals, assists per named player."""

    sheets = {sheet.team_id: sheet.team_name for sheet in team_sheets_from_rundown(rundown)}
    rows: list[dict[str, object]] = []
    for profile in named_player_profiles(rundown):
        rows.append(
            {
                "Team": sheets.get(profile.team_id, rundown.summary.home_team_name),
                "Player": profile.player_name or "Player",
                "#": profile.jersey_number or "—",
                "Pos": profile.position or "—",
                "Minutes": round(profile.offensive.minutes, 1),
                "Goals": profile.offensive.goals,
                "Assists": profile.offensive.assists,
                "Shots": profile.offensive.total_shots,
                "Passes": profile.distribution.passes.total,
            }
        )
    rows.sort(key=lambda row: (str(row["Team"]), row["#"] == "—", row["#"] or 99))
    return rows


def render_collective_section(rundown: MatchRundown) -> None:
    """Team-level Impact / Spiideo boards."""

    perspective = analysis_perspective(rundown)
    tag_source = getattr(rundown.summary, "tag_source", "official") or "official"
    if perspective.one_sided and tag_source != "film":
        st.info(
            f"This XML is {perspective.analysed_team_name}'s analysis only. "
            "Away XML is optional if you want a full two-team board."
        )
        profile = analysed_team_profile(rundown)
        if profile is not None:
            st.subheader(f"{perspective.analysed_team_name} collective stats")
            st.caption(
                "Offensive = analysed team. Defensive = analysed team defending. "
                "Same four Spiideo pillars as the individual sheets."
            )
            render_dashboard(load_from_team_profile(rundown, profile), collective=True)
        return
    teams = team_profiles_from_rundown(rundown)
    if not teams:
        return
    st.subheader("Collective team stats")
    st.caption(
        "Every tagged event for a side folded into offensive, defensive, "
        "distribution, and possession."
    )
    tabs = st.tabs([profile.player_name for profile in teams])
    for tab, profile in zip(tabs, teams, strict=True):
        with tab:
            render_dashboard(load_from_team_profile(rundown, profile), collective=True)


def render_individual_section(rundown: MatchRundown) -> None:
    """Player list + full individual pillar sheet."""

    named = named_player_profiles(rundown)
    if not named:
        st.info(
            "No named individual sheets on this collect. "
            "Invented film player names (Home CM 4) are draft labels — "
            "edit line-ups in OnceSport after import if needed."
        )
        return
    st.subheader("Individual players")
    st.caption(
        "Everyone who was tagged in the match. Minutes are match-clock "
        "minutes from tags (not true on/off unless you tag substitutions)."
    )
    st.dataframe(_individual_player_rows(rundown), hide_index=True, width="stretch")
    player_map = {profile_label(profile): profile.player_id for profile in named}
    player_label = st.selectbox("Open player sheet", options=list(player_map.keys()))
    load = load_from_rundown(rundown, player_map[player_label])
    render_dashboard(load, collective=False, show_chrome=False)


def render_review_section(rundown: MatchRundown) -> None:
    """Step D: fix only wrong tags, then export uses the cleaned sheet."""

    st.subheader("Review tags")
    st.caption(
        "The computer already tagged the match. "
        "Only change rows that look wrong — untick Keep to delete, "
        "or change Tag / Player. Then press Apply. "
        "You do **not** re-tag the whole game by hand."
    )
    labels = list(player_label_map(rundown).keys()) or ["—"]
    tag_choices = event_type_choices()
    base_rows = review_rows(rundown)
    edited = st.data_editor(
        base_rows,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        column_config={
            "keep": st.column_config.CheckboxColumn("Keep", default=True),
            "event_id": None,
            "clock": st.column_config.TextColumn("Clock", disabled=True),
            "tag": st.column_config.SelectboxColumn("Tag", options=tag_choices),
            "player": st.column_config.SelectboxColumn("Player", options=labels),
            "x": st.column_config.NumberColumn("X", disabled=True),
            "y": st.column_config.NumberColumn("Y", disabled=True),
            "goal": st.column_config.CheckboxColumn("Goal", disabled=True),
        },
        key="review_tags_editor",
    )
    apply = st.button("Apply review fixes", type="primary", use_container_width=True)
    if apply:
        try:
            updated = apply_review_edits(rundown, edited)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state[RUNDOWN_KEY] = rundown_to_json(updated)
        history_msg = _remember_collection(updated)
        st.session_state[PERSIST_KEY] = f"Review saved · {history_msg}"
        st.success(
            f"Saved {updated.summary.event_count} tags "
            f"(was {rundown.summary.event_count}). Export buttons use this sheet."
        )
        st.rerun()

    st.subheader("5-minute coverage")
    st.caption(
        "Each row is a 5-minute chunk. "
        "“re-pass” means the computer already watched that chunk twice "
        "because it looked thin."
    )
    st.dataframe(coverage_rows_for_rundown(rundown), hide_index=True, width="stretch")


def render_collective_rundown(rundown: MatchRundown) -> None:
    """Match page: summary, then Collective / Individual / Review views."""

    render_match_summary(rundown)
    render_pdf_download(rundown, key="match_pdf_report")
    collective_tab, individual_tab, review_tab = st.tabs(
        ["Collective", "Individual", "Review tags"]
    )
    with collective_tab:
        render_collective_section(rundown)
    with individual_tab:
        render_individual_section(rundown)
    with review_tab:
        render_review_section(rundown)


def render_history() -> None:
    """Browse auto-saved collection history; reopen matches or download PDFs."""

    st.subheader("Collection history")
    entries = list_history()
    if not entries:
        st.info(
            "No collections saved yet. Analyse a match or load the sample — "
            "it will appear here as soon as collect finishes."
        )
        return
    for entry in entries:
        with st.container(border=True):
            left, mid, right = st.columns([3, 1, 1])
            with left:
                st.markdown(f"**{entry.label}**")
                st.caption(
                    f"Saved {entry.saved_at}  ·  "
                    f"{entry.event_count} events  ·  "
                    f"{entry.player_count} players  ·  "
                    f"source `{entry.tag_source}`"
                )
            with mid:
                if st.button(
                    "Open match",
                    key=f"history_open_{entry.history_id}",
                    use_container_width=True,
                ):
                    loaded = load_history_rundown(entry.history_id)
                    if loaded is None:
                        st.error("Could not load this saved collect.")
                    else:
                        st.session_state[RUNDOWN_KEY] = rundown_to_json(loaded)
                        st.session_state.pop("analyse_cleared", None)
                        _go_match()
                        st.rerun()
                if st.button(
                    "Delete",
                    key=f"history_delete_{entry.history_id}",
                    use_container_width=True,
                ):
                    delete_history_entry(entry.history_id)
                    st.rerun()
            with right:
                loaded = load_history_rundown(entry.history_id)
                if loaded is not None:
                    try:
                        pdf_bytes = build_match_report_pdf(loaded, history=entry)
                    except Exception as exc:  # noqa: BLE001
                        st.caption(f"PDF unavailable ({exc}).")
                    else:
                        st.download_button(
                            "PDF report",
                            data=pdf_bytes,
                            file_name=_pdf_filename(entry.label),
                            mime="application/pdf",
                            use_container_width=True,
                            key=f"history_pdf_{entry.history_id}",
                        )
                else:
                    st.caption("File missing")


def render_api_demo(base_url: str, fetch: FetchFn) -> None:
    """Optional catalog profile when no match has been analysed yet."""

    with st.expander("API demo match (no upload)", expanded=False):
        matches = _match_options()
        match_label = st.selectbox("Match UUID", options=list(matches.keys()))
        match_id = matches[match_label]
        players = _player_options(match_id)
        player_label = st.selectbox("Player ID", options=list(players.keys()))
        player_id = players[player_label]
        st.caption("Loads `/api/v1/matches/{id}/players/{id}` or a dummy fallback.")
        render_dashboard(fetch(base_url, match_id, player_id))


def _site_password_ok() -> bool:
    """Optional shared password for the Railway website (STATMAN_SITE_PASSWORD)."""

    expected = os.environ.get("STATMAN_SITE_PASSWORD", "").strip()
    if not expected:
        return True
    if st.session_state.get("_statman_authed"):
        return True
    st.markdown("### StatMan")
    st.caption("Restricted access")
    entered = st.text_input("Password", type="password", key="statman_site_password")
    if st.button("Enter", type="primary", use_container_width=True):
        if entered == expected:
            st.session_state["_statman_authed"] = True
            st.rerun()
        st.error("Wrong password.")
    return False


def main(*, fetch: FetchFn = _run_fetch) -> None:
    """Streamlit entry point. ``fetch`` is injectable for tests."""

    st.set_page_config(
        page_title="StatMan · Analyse Stats",
        page_icon="⚽",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_styles()
    if not _site_password_ok():
        return
    # On Railway, skip local Uvicorn — the public port is Streamlit only.
    # Blocking here for 15s caused Railway's "Application failed to respond".
    if not _use_streamlit_film_upload():
        ensure_api_running(wait_s=15.0)
        _ensure_api_watchdog()
    _hydrate_collect_job()
    base_url = render_sidebar()
    rundown = _stored_rundown()
    job_path_raw = str(st.session_state.get(JOB_KEY, "") or "")
    job_status = read_job_status(Path(job_path_raw)) if job_path_raw else None
    analysing = bool(job_status and job_status.get("state") in {"queued", "claimed", "running"})
    section = render_app_nav(has_match=rundown is not None, analysing=analysing)

    if section == "Match rundown" and rundown is not None:
        render_collective_rundown(rundown)
        return

    if section == "History":
        render_history()
        return

    if section == "Analysing" and analysing and job_status is not None:
        render_job_progress(job_status)
        time.sleep(8)
        st.rerun()
        return

    if job_status and job_status.get("state") == "error":
        st.error(str(job_status.get("error") or "Background collect failed."))

    render_analyse_landing(base_url)
    render_api_demo(base_url, fetch)


if __name__ == "__main__":
    main()
