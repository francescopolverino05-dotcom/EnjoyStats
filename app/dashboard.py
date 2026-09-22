"""EnjoyStats Streamlit dashboard — Analyse Stats plus collective team pillars.

Launch from the repository root::

    streamlit run app/dashboard.py
"""

from __future__ import annotations

import asyncio
import importlib
import math
import sys
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

from app.client import DEFAULT_BASE_URL, ProfileLoad, fetch_player_profile
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
    collect_official_two_team,
    collect_sample_match,
    collect_uploaded_bytes,
    film_has_official_tags,
    load_from_rundown,
    load_from_team_profile,
    persist_rundown,
    profile_label,
    ready_films,
    save_uploaded_film,
)
from analytics.collect_job import (
    latest_job_status_path,
    load_job_rundown,
    read_job_status,
    start_collect_job,
)
from analytics.film_link import register_match_link
from analytics.team_collect import (
    is_one_sided_sheet,
    named_player_profiles,
    team_profiles_from_rundown,
)
from analytics.video_auto_collect import (
    VideoCollectError,
    film_inbox_dir,
    film_upload_dir,
    normalize_film_path,
    video_limit_label,
)
from api.film_upload import upload_page_html
from analytics.game_ingest import MatchRundown, rundown_from_mapping, rundown_to_json
from analytics.team_sheet import (
    highlight_moments_from_rundown,
    tag_inventory_rows,
    team_sheet_rows,
    team_sheets_from_rundown,
)
from analytics.match_tags import attacks_from_events, rundown_to_csv, rundown_to_xml
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
            f"#{profile.jersey_number or '—'} {profile.player_name or profile.position or 'Player'}",
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
            st.title("EnjoyStats")
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
    st.caption("Watching every sampled frame of the film on disk. Keep this tab open — a full match can take hours.")
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


def render_match_summary(rundown: MatchRundown) -> None:
    """Headline match totals collected from the uploaded event feed."""

    summary = rundown.summary
    st.title("EnjoyStats")
    st.subheader("Match rundown")
    st.caption(
        "These tags are already collected — an analyst does not have to click "
        "every shot, pass, or corner. Official XML is the scoresheet; film "
        "Analyse Stats is the time-saver for both teams."
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Events", summary.event_count)
    c2.metric("Players", summary.player_count)
    c3.metric("Goals", summary.goals)
    c4.metric("Shots", summary.shots)
    c5.metric("Passes", summary.passes)
    st.caption(
        f"Match `{summary.match_id}`  ·  {summary.duration_minutes:.1f} minutes of collected play. "
        "Every number below is counted from the match tags (passes, shots, "
        "recoveries) — the same sheet Spiideo / Wyscout XML export uses."
    )
    render_tag_inventory(rundown)
    render_team_sheet(rundown)
    render_match_tags(rundown)
    render_highlight_moments(rundown)


def render_tag_inventory(rundown: MatchRundown) -> None:
    """Show how many of each action the machine (or official XML) already tagged."""

    rows = tag_inventory_rows(rundown)
    if not rows:
        return
    st.subheader("What you no longer have to tag")
    st.caption(
        "Each row is one action type an analyst would otherwise mark by hand. "
        "Totals are the collected sheet — download CSV or XML below to take "
        "this into the rest of the data-collection workflow."
    )
    st.dataframe(rows, hide_index=True, width="stretch")


def render_team_sheet(rundown: MatchRundown) -> None:
    """Impact-style 15-stat board, one column per team."""

    sheets = team_sheets_from_rundown(rundown)
    if not sheets:
        return
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
    xml_col, csv_col = st.columns(2)
    with xml_col:
        st.download_button(
            "Download match tags (XML)",
            data=rundown_to_xml(rundown),
            file_name="match_tags.xml",
            mime="application/xml",
            use_container_width=True,
        )
    with csv_col:
        st.download_button(
            "Download match tags (CSV)",
            data=rundown_to_csv(rundown),
            file_name="match_tags.csv",
            mime="text/csv",
            use_container_width=True,
        )


def _resolve_film_source(
    film_path: str,
    inbox_path: Path | None,
    film: object | None,
    upload_dir: Path,
    update: Callable[[str, float], None],
) -> str:
    """Pick the on-disk film: pasted path, inbox file, or saved browser pick."""

    if film_path.strip().strip("'\"").strip():
        return str(normalize_film_path(film_path))
    if inbox_path is not None:
        return str(inbox_path)
    if film is None:
        raise ValueError(UPLOAD_DISCONNECT_HINT)
    suffix = Path(getattr(film, "name", "match.mp4")).suffix or ".mp4"
    dest = upload_dir / f"upload{suffix.lower()}"

    def _on_save(written: int, expected: int) -> None:
        denom = expected if expected > 0 else max(written, 1)
        update(
            f"Saving upload {_format_bytes(written)}"
            + (f" / {_format_bytes(expected)}" if expected > 0 else ""),
            0.35 * (written / denom),
        )

    update("Saving upload to disk…", 0.02)
    save_uploaded_film(film, dest, on_progress=_on_save)
    return str(dest)


RUNDOWN_KEY = "collected_rundown"
PERSIST_KEY = "ingest_persist_message"
JOB_KEY = "collect_job_path"


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
            st.session_state[PERSIST_KEY] = str(status.get("label") or "Background collect ready.")
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
        st.session_state[PERSIST_KEY] = message
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

    st.sidebar.header("EnjoyStats")
    st.sidebar.caption(
        "Skip hand-tagging every shot, pass, and corner. "
        "Register a film or drop official XML, then Analyse Stats. "
        "A full film can take hours — the tag sheet is waiting when you come back."
    )
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

    error: str | None = None
    if collect_sample:
        error = _load_sample_match(base_url)
    elif collect_json:
        if uploaded_json is None:
            error = "Choose a JSON or XML tag file first, or use Analyse Stats on the main page."
        else:
            try:
                rundown = collect_uploaded_bytes(uploaded_json.getvalue())
                st.session_state[RUNDOWN_KEY] = rundown_to_json(rundown)
                _persisted, message = asyncio.run(persist_rundown(base_url, rundown))
                st.session_state[PERSIST_KEY] = message
            except ValueError as exc:
                error = str(exc)
    if error:
        st.sidebar.error(error)

    rundown = _stored_rundown()
    if rundown is not None:
        st.sidebar.success(
            f"Collected {rundown.summary.event_count} events · "
            f"{rundown.summary.goals} goals"
        )
        persist_message = st.session_state.get(PERSIST_KEY)
        if persist_message:
            st.sidebar.caption(str(persist_message))
    return base_url


def render_job_progress(status: dict[str, object]) -> None:
    """Main-panel watch for a hours-long background analyse."""

    st.title("EnjoyStats")
    st.subheader("Analysing match")
    st.caption(
        "Watching the whole film at Wyscout tag density. "
        "You can close this tab — come back later and refresh. "
        "This page also rechecks while it stays open."
    )
    fraction = float(status.get("fraction") or 0.0)
    label = str(status.get("label") or "Watching the film…")
    st.progress(min(1.0, max(0.0, fraction)), text=label)
    st.caption(f"{fraction * 100:.0f}%  ·  {label}")
    film = str(status.get("film") or "")
    if film:
        st.caption(f"Film: `{Path(film).name}`")


def render_film_uploader_panel(base_url: str) -> None:
    """Chunked FastAPI uploader for large films on any device."""

    upload_url = f"{base_url.rstrip('/')}/upload-film"
    inbox = film_inbox_dir()
    st.markdown("**Upload from this device**")
    st.caption(
        "Saves the film in 4 MB chunks to "
        f"`{inbox}` so any phone, tablet, or computer can send a full match "
        f"(up to {video_limit_label()}) without Streamlit's large-PUT disconnect. "
        "Then click Analyse Stats."
    )
    st.link_button("Open uploader in a new tab", upload_url)
    import streamlit.components.v1 as components

    components.html(upload_page_html(base_url.rstrip("/")), height=420, scrolling=False)


def render_official_tag_section(base_url: str) -> None:
    """Upload a two-team export, or merge Home + Away one-team analyses."""

    st.subheader("Official two-team tag sheet")
    st.caption(
        "Fastest path when Wyscout / Spiideo / Nacsport already tagged the "
        "match: drop one two-team export, or Home analysis XML plus Away "
        "analysis XML. Both sides keep their real shots, passes, and corners. "
        "That is the official scoresheet — not film computer vision."
    )
    two_team = st.file_uploader(
        "One two-team export (JSON, MatchTags, or Wyscout XML)",
        type=["json", "xml"],
        key="official_two_team",
    )
    home_xml = st.file_uploader(
        "Or Home one-team analysis XML",
        type=["xml"],
        key="official_home_xml",
    )
    away_xml = st.file_uploader(
        "And Away one-team analysis XML",
        type=["xml"],
        key="official_away_xml",
    )
    collect = st.button("Collect official tags", use_container_width=True)
    if not collect:
        return
    try:
        if two_team is not None:
            rundown = collect_official_two_team(two_team.getvalue())
        elif home_xml is not None and away_xml is not None:
            rundown = collect_official_two_team(home_xml.getvalue(), away_xml.getvalue())
        elif home_xml is not None:
            rundown = collect_official_two_team(home_xml.getvalue())
        else:
            raise ValueError(
                "Upload one two-team export, or both Home and Away analysis XMLs."
            )
        st.session_state[RUNDOWN_KEY] = rundown_to_json(rundown)
        st.session_state.pop("analyse_cleared", None)
        _persisted, message = asyncio.run(persist_rundown(base_url, rundown))
        st.session_state[PERSIST_KEY] = message
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))


def render_analyse_landing(base_url: str) -> None:
    """Impact concept: official tags, or register a film and Analyse Stats."""

    inbox_dir = film_inbox_dir()
    upload_dir = film_upload_dir()
    st.title("EnjoyStats")
    st.caption(
        "The job is to cut the hours an analyst spends tagging. "
        "If official XML exists, collect it. If you only have the film, "
        "Analyse Stats tags shots, passes, corners, throw-ins, recoveries, "
        "and the rest for both teams so data collection starts from a "
        "sheet — not from a blank timeline."
    )
    if st.button("Load sample match", use_container_width=True, key="landing_sample"):
        sample_error = _load_sample_match(base_url)
        if sample_error:
            st.error(sample_error)
        else:
            st.rerun()
    render_official_tag_section(base_url)
    st.subheader("Analyse Stats")
    st.caption(
        "No official sheet? Upload the match film (up to "
        f"{video_limit_label()}). Analyse Stats watches both teams and "
        "tags shots, passes, corners, and the rest so an analyst does not "
        "have to do that by hand. Walk away — a full 90 minutes can take "
        "hours. Come back to a Home / Away tag inventory plus collective "
        "pillars. This saves tagging time; it is not a Wyscout scoresheet."
    )
    link = st.text_input(
        "Register a link",
        value="",
        placeholder="https://…  ·  file:///…  ·  or a local path",
        help="Direct video URL, local path, file://, or YouTube/Vimeo when yt-dlp is installed.",
    ).strip()
    on_disk = ready_films()
    none_label = "(none — register a link or upload below)"
    disk_labels: dict[str, Path | None] = {none_label: None}
    for path in on_disk:
        disk_labels[f"{path.name}  ·  {_format_bytes(path.stat().st_size)}"] = path
    inbox_choice = st.selectbox("Films and tag sheets on this machine", options=list(disk_labels.keys()))
    inbox_path = disk_labels[inbox_choice]
    film = st.file_uploader(
        "Or upload from this phone, tablet, or computer",
        type=["mp4", "mov", "mkv", "avi", "m4v", "webm", "xml"],
        help=(
            "Clips and XML work here. For a full match (up to "
            f"{video_limit_label()}) use the chunked uploader below."
        ),
    )
    with st.expander("Large film uploader (any phone, tablet, or computer)", expanded=False):
        render_film_uploader_panel(base_url)
    analyse = st.button("Analyse Stats", type="primary", use_container_width=True)

    if not analyse:
        return
    try:
        source_path = ""
        pending_upload = None
        if link:
            update, finish = render_upload_loader()
            update("Registering match link…", 0.08)
            registered = register_match_link(link, inbox_dir)
            source_path = str(registered)
            finish()
        elif inbox_path is not None:
            source_path = str(inbox_path)
        elif film is not None:
            pending_upload = film
        else:
            raise ValueError(
                "Register a link, pick a film on this machine, or upload a file first."
            )
        update, finish = render_upload_loader()
        update("Preparing the match…", 0.04)
        if pending_upload is not None:
            source_path = _resolve_film_source("", None, pending_upload, upload_dir, update)
        source = Path(source_path)
        if film_has_official_tags(source):
            update("Collecting official tags…", 0.36)

            def _on_collect(label: str, fraction: float) -> None:
                update(label, 0.36 + 0.64 * fraction)

            rundown = collect_from_film_path(source_path, on_progress=_on_collect)
            finish()
            st.session_state[RUNDOWN_KEY] = rundown_to_json(rundown)
            st.session_state.pop("analyse_cleared", None)
            _persisted, message = asyncio.run(persist_rundown(base_url, rundown))
            st.session_state[PERSIST_KEY] = message
            st.rerun()
        else:
            update("Starting background analyse…", 0.2)
            status_path = start_collect_job(source)
            st.session_state[JOB_KEY] = str(status_path)
            st.session_state.pop(RUNDOWN_KEY, None)
            st.session_state.pop("analyse_cleared", None)
            st.session_state[PERSIST_KEY] = (
                "Full-match analyse is running in the background. "
                "Walk away and refresh later. "
                f"Status: {status_path.name}"
            )
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


def render_collective_rundown(rundown: MatchRundown) -> None:
    """Home / Away Spiideo pillars counted from the match tags."""

    render_match_summary(rundown)
    if is_one_sided_sheet(rundown):
        analysed = rundown.summary.home_team_name or "Home"
        other = rundown.summary.away_team_name or "Away"
        st.info(
            f"This official sheet is a one-team analysis of {analysed}. "
            f"{other} only has tags that appear on this export — usually the "
            "goal they scored. Upload {other}'s analysis XML next to this one "
            "under Official two-team tag sheet to keep both sides official."
        )
    teams = team_profiles_from_rundown(rundown)
    if teams:
        st.subheader("Collective team stats")
        st.caption(
            "Every tagged event for a side is folded into the same four "
            "pillars Spiideo publishes (offensive / construction, defending, "
            "distribution, possession). Substitutions do not split the sheet — "
            "there are still two teams for the full match."
        )
        tabs = st.tabs([profile.player_name for profile in teams])
        for tab, profile in zip(tabs, teams, strict=True):
            with tab:
                load = load_from_team_profile(rundown, profile)
                render_dashboard(load, collective=True)
    named = named_player_profiles(rundown)
    if named:
        with st.expander("Named player sheets (official tags)", expanded=False):
            st.caption(
                "Individual rows from a Wyscout / Nacsport sheet. "
                "Film-only collects hide invented Home CM 4 identities."
            )
            player_map = {profile_label(profile): profile.player_id for profile in named}
            player_label = st.selectbox("Named player", options=list(player_map.keys()))
            load = load_from_rundown(rundown, player_map[player_label])
            render_dashboard(load, collective=False, show_chrome=False)


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


def main(*, fetch: FetchFn = _run_fetch) -> None:
    """Streamlit entry point. ``fetch`` is injectable for tests."""

    st.set_page_config(
        page_title="EnjoyStats · Analyse Stats",
        page_icon="⚽",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_styles()
    _hydrate_collect_job()
    base_url = render_sidebar()
    rundown = _stored_rundown()
    if rundown is not None:
        render_collective_rundown(rundown)
        return

    job_path_raw = str(st.session_state.get(JOB_KEY, "") or "")
    if job_path_raw:
        status = read_job_status(Path(job_path_raw))
        if status and status.get("state") in {"queued", "running"}:
            render_job_progress(status)
            time.sleep(8)
            st.rerun()
            return
        if status and status.get("state") == "error":
            st.title("EnjoyStats")
            st.error(str(status.get("error") or "Background collect failed."))

    render_analyse_landing(base_url)
    render_api_demo(base_url, fetch)


if __name__ == "__main__":
    main()
