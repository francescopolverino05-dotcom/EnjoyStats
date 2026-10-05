"""Build a downloadable PDF report from a collected match rundown."""

from __future__ import annotations

import io
from datetime import datetime, timezone

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib import pyplot as plt

from analytics.collection_history import HistoryEntry
from analytics.game_ingest import MatchRundown
from analytics.team_collect import named_player_profiles, team_profiles_from_rundown
from analytics.team_sheet import team_sheet_rows, team_sheets_from_rundown


def _page_table(
    pdf: PdfPages,
    *,
    title: str,
    subtitle: str,
    columns: list[str],
    rows: list[list[str]],
) -> None:
    fig, ax = plt.subplots(figsize=(11.69, 8.27))  # A4 landscape-ish
    ax.axis("off")
    fig.patch.set_facecolor("#f8fafc")
    ax.text(0.02, 0.96, title, fontsize=16, fontweight="bold", transform=ax.transAxes)
    ax.text(0.02, 0.91, subtitle, fontsize=10, color="#475569", transform=ax.transAxes)
    if not rows:
        ax.text(0.02, 0.8, "No rows for this section.", fontsize=11, transform=ax.transAxes)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)
        return
    table = ax.table(
        cellText=rows,
        colLabels=columns,
        loc="upper left",
        cellLoc="left",
        colLoc="left",
        bbox=[0.02, 0.05, 0.96, 0.82],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.25)
    for (row_i, _col_i), cell in table.get_celld().items():
        if row_i == 0:
            cell.set_facecolor("#0f172a")
            cell.set_text_props(color="white", fontweight="bold")
        elif row_i % 2 == 0:
            cell.set_facecolor("#e2e8f0")
        cell.set_edgecolor("#cbd5e1")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def build_match_report_pdf(
    rundown: MatchRundown,
    *,
    history: HistoryEntry | None = None,
) -> bytes:
    """Return a multi-page PDF: cover, team board, individuals."""

    home = rundown.summary.home_team_name or "Home"
    away = rundown.summary.away_team_name or "Away"
    sheets = team_sheets_from_rundown(rundown)
    if len(sheets) >= 2:
        home, away = sheets[0].team_name, sheets[1].team_name
        score = f"{sheets[0].goals}–{sheets[1].goals}"
    elif history is not None:
        home, away = history.home_team_name, history.away_team_name
        score = f"{history.home_goals}–{history.away_goals}"
    else:
        score = str(rundown.summary.goals)
    saved = history.saved_at if history is not None else datetime.now(timezone.utc).isoformat()
    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        fig.patch.set_facecolor("#f8fafc")
        ax.text(0.5, 0.72, "EnjoyStats", fontsize=28, fontweight="bold", ha="center")
        ax.text(0.5, 0.62, "Match statistics report", fontsize=14, ha="center", color="#475569")
        ax.text(
            0.5,
            0.48,
            f"{home}  {score}  {away}",
            fontsize=20,
            fontweight="bold",
            ha="center",
        )
        ax.text(
            0.5,
            0.38,
            (
                f"Events {rundown.summary.event_count}  ·  "
                f"Players {rundown.summary.player_count}  ·  "
                f"Source {getattr(rundown.summary, 'tag_source', 'official')}"
            ),
            fontsize=11,
            ha="center",
            color="#334155",
        )
        ax.text(0.5, 0.28, f"Saved {saved}", fontsize=10, ha="center", color="#64748b")
        ax.text(
            0.5,
            0.18,
            f"Match `{rundown.match_id}`",
            fontsize=9,
            ha="center",
            color="#94a3b8",
        )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        team_rows_raw = team_sheet_rows(sheets)
        if team_rows_raw:
            columns = list(team_rows_raw[0].keys())
            rows = [[str(row.get(col, "")) for col in columns] for row in team_rows_raw]
            _page_table(
                pdf,
                title="Collective team statistics",
                subtitle="Impact-style board counted from match tags",
                columns=columns,
                rows=rows,
            )

        for profile in team_profiles_from_rundown(rundown):
            rows = [
                ["Goals", str(profile.offensive.goals)],
                ["Assists", str(profile.offensive.assists)],
                ["Total shots", str(profile.offensive.total_shots)],
                ["Shots on target", str(profile.offensive.shots_on_target)],
                ["Passes", str(profile.distribution.passes.total)],
                [
                    "Pass accuracy",
                    f"{profile.distribution.passes.success_rate:.0%}",
                ],
                ["Corners", str(profile.offensive.corners)],
                ["Interceptions", str(profile.defensive.interceptions.total)],
                ["Recoveries", str(profile.defensive.ball_recoveries.total)],
                ["Goals against", str(profile.defensive.goals_against)],
                ["Possession %", f"{profile.possession.percentage:.1f}"],
            ]
            _page_table(
                pdf,
                title=f"Collective · {profile.player_name}",
                subtitle="Spiideo four-pillar headline numbers",
                columns=["Stat", "Value"],
                rows=rows,
            )

        named = named_player_profiles(rundown)
        if named:
            sheets_by_id = {sheet.team_id: sheet.team_name for sheet in sheets}
            rows = []
            for profile in named:
                rows.append(
                    [
                        sheets_by_id.get(profile.team_id, ""),
                        str(profile.jersey_number or ""),
                        profile.player_name or "Player",
                        profile.position or "",
                        f"{profile.offensive.minutes:.1f}",
                        str(profile.offensive.goals),
                        str(profile.offensive.assists),
                        str(profile.offensive.total_shots),
                        str(profile.distribution.passes.total),
                    ]
                )
            _page_table(
                pdf,
                title="Individual players",
                subtitle="Minutes are match-clock from tags",
                columns=[
                    "Team",
                    "#",
                    "Player",
                    "Pos",
                    "Min",
                    "G",
                    "A",
                    "Shots",
                    "Passes",
                ],
                rows=rows,
            )

    return buffer.getvalue()
