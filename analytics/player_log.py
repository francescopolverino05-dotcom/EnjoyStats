"""Individual Statistics log rows shared by the dashboard and Excel export."""

from __future__ import annotations

from analytics.game_ingest import MatchRundown
from analytics.team_collect import named_player_profiles
from analytics.team_sheet import team_sheets_from_rundown
from data_models.player_stats import PlayerMatchProfile


def players_for_stat_log(rundown: MatchRundown) -> list[PlayerMatchProfile]:
    """Named line-up players when present; otherwise film roster labels."""

    named = named_player_profiles(rundown)
    if named:
        return named
    return list(rundown.players)


def individual_stat_log_rows(rundown: MatchRundown) -> list[dict[str, object]]:
    """Impact Individual Statistics: one row per player with headline numbers."""

    sheets = {sheet.team_id: sheet.team_name for sheet in team_sheets_from_rundown(rundown)}
    rows: list[dict[str, object]] = []
    for profile in players_for_stat_log(rundown):
        passes = profile.distribution.passes
        pass_acc = round(100.0 * passes.success_rate, 1) if passes.total else 0.0
        rows.append(
            {
                "Team": sheets.get(profile.team_id, rundown.summary.home_team_name),
                "Player": profile.player_name or "Player",
                "#": profile.jersey_number or "—",
                "Pos": profile.position or "—",
                "Min": round(profile.offensive.minutes, 1),
                "G": profile.offensive.goals,
                "A": profile.offensive.assists,
                "Shots": profile.offensive.total_shots,
                "SoT": profile.offensive.shots_on_target,
                "Passes": passes.total,
                "Pass %": pass_acc,
                "Prog": profile.distribution.progressive_passes.total,
                "Cross": profile.distribution.crosses.total,
                "Rec": profile.defensive.ball_recoveries.total,
                "Int": profile.defensive.interceptions.total,
                "Fouls": profile.defensive.fouls.committed,
            }
        )
    rows.sort(
        key=lambda row: (
            str(row["Team"]),
            -(int(row["G"]) + int(row["A"])),
            -(int(row["Passes"])),
            row["#"] == "—",
            row["#"] or 99,
        )
    )
    return rows
