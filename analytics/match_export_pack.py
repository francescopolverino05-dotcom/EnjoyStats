"""Share-friendly exports: Excel workbook, highlights CSV, download zip pack."""

from __future__ import annotations

import csv
import io
import zipfile
from xml.sax.saxutils import escape

from analytics.game_ingest import MatchRundown
from analytics.match_tags import rundown_to_csv
from analytics.offball_iq import (
    offball_player_rows,
    offball_player_stats,
    offball_team_rows,
    offball_team_stats,
)
from analytics.player_log import individual_stat_log_rows
from analytics.team_sheet import (
    highlight_moments_from_rundown,
    team_sheet_rows,
    team_sheets_from_rundown,
)


def _xlsx_sheet_xml(name: str, rows: list[list[object]]) -> str:
    """Build one worksheet XML body (sharedStrings avoided — inline strings)."""

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for r_index, row in enumerate(rows, start=1):
        lines.append(f'<row r="{r_index}">')
        for c_index, value in enumerate(row):
            col = _col_letter(c_index)
            cell_ref = f"{col}{r_index}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                lines.append(f'<c r="{cell_ref}"><v>{value}</v></c>')
            else:
                text = escape(str(value if value is not None else ""))
                lines.append(
                    f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>'
                )
        lines.append("</row>")
    lines.append("</sheetData></worksheet>")
    return "\n".join(lines)


def _col_letter(index: int) -> str:
    n = index + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = []
    for index, name in enumerate(sheet_names, start=1):
        safe = escape(name[:31] or f"Sheet{index}")
        sheets.append(
            f'<sheet name="{safe}" sheetId="{index}" r:id="rId{index}"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(sheets)}</sheets></workbook>"
    )


def _rels_xml(count: int) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for index in range(1, count + 1):
        parts.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
    parts.append("</Relationships>")
    return "\n".join(parts)


def _content_types_xml(count: int) -> str:
    overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    ]
    for index in range(1, count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _dict_rows_to_matrix(rows: list[dict[str, object]]) -> list[list[object]]:
    if not rows:
        return [["(empty)"]]
    headers = list(rows[0].keys())
    matrix: list[list[object]] = [headers]
    for row in rows:
        matrix.append([row.get(key, "") for key in headers])
    return matrix


def build_match_excel(rundown: MatchRundown) -> bytes:
    """Excel workbook: Match Statistics, Individual, Off-ball, Highlights."""

    sheets_data: list[tuple[str, list[list[object]]]] = []
    team_rows = team_sheet_rows(team_sheets_from_rundown(rundown))
    sheets_data.append(("Match Statistics", _dict_rows_to_matrix(team_rows)))
    individual = individual_stat_log_rows(rundown)
    sheets_data.append(("Individual Statistics", _dict_rows_to_matrix(individual)))
    off_teams = offball_team_rows(offball_team_stats(rundown))
    sheets_data.append(("Off-ball IQ", _dict_rows_to_matrix(off_teams)))
    off_players = offball_player_rows(offball_player_stats(rundown))
    sheets_data.append(("Player Influence IQ", _dict_rows_to_matrix(off_players)))
    moments = highlight_moments_from_rundown(rundown)
    highlight_rows = [
        {
            "Clock": moment.clock,
            "Moment": moment.kind,
            "Player": moment.player,
            "Team": moment.team_name,
            "Start ms": moment.start_ms,
            "End ms": moment.end_ms,
        }
        for moment in moments
    ]
    sheets_data.append(("Highlights 15s", _dict_rows_to_matrix(highlight_rows)))

    names = [name for name, _ in sheets_data]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(names)))
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml(names))
        archive.writestr("xl/_rels/workbook.xml.rels", _rels_xml(len(names)))
        for index, (_name, matrix) in enumerate(sheets_data, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _xlsx_sheet_xml(f"sheet{index}", matrix),
            )
    return buffer.getvalue()


def highlights_to_csv(rundown: MatchRundown) -> str:
    """CSV of 15-second highlight windows (Impact-style clip list)."""

    moments = highlight_moments_from_rundown(rundown)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Clock", "Moment", "Player", "Team", "Start ms", "End ms", "Duration ms"])
    for moment in moments:
        writer.writerow(
            [
                moment.clock,
                moment.kind,
                moment.player,
                moment.team_name,
                moment.start_ms,
                moment.end_ms,
                moment.end_ms - moment.start_ms,
            ]
        )
    return buffer.getvalue()


def build_share_pack(rundown: MatchRundown) -> bytes:
    """Zip pack: Excel + highlights CSV + full tag CSV (share/download friendly)."""

    home = rundown.summary.home_team_name or "Home"
    away = rundown.summary.away_team_name or "Away"
    stem = f"{home}_vs_{away}".replace(" ", "_")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}_stats.xlsx", build_match_excel(rundown))
        archive.writestr(f"{stem}_highlights.csv", highlights_to_csv(rundown))
        archive.writestr(f"{stem}_tags.csv", rundown_to_csv(rundown))
        off_rows = offball_team_rows(offball_team_stats(rundown))
        off_buf = io.StringIO()
        if off_rows:
            writer = csv.DictWriter(off_buf, fieldnames=list(off_rows[0].keys()))
            writer.writeheader()
            writer.writerows(off_rows)
        archive.writestr(f"{stem}_offball_iq.csv", off_buf.getvalue())
    return buffer.getvalue()
