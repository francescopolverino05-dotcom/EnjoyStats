"""Match line-ups: real names + shirt numbers for film collect."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from io import StringIO
from typing import Any


@dataclass(frozen=True, slots=True)
class LineupPlayer:
    """One player on a team sheet."""

    side: str  # "home" | "away"
    jersey: int
    name: str
    position: str = ""


@dataclass(frozen=True, slots=True)
class MatchLineups:
    """Home + Away sheets for one fixture."""

    home_team: str
    away_team: str
    home: tuple[LineupPlayer, ...]
    away: tuple[LineupPlayer, ...]

    def for_side(self, side: str) -> tuple[LineupPlayer, ...]:
        return self.home if side == "home" else self.away

    def by_jersey(self, side: str, jersey: int) -> LineupPlayer | None:
        for player in self.for_side(side):
            if player.jersey == jersey:
                return player
        return None


_SIDE_ALIASES = {
    "h": "home",
    "home": "home",
    "1": "home",
    "casa": "home",
    "a": "away",
    "away": "away",
    "2": "away",
    "ospite": "away",
    "trasferta": "away",
}

_JERSEY_KEYS = (
    "jersey",
    "number",
    "shirt",
    "shirtnumber",
    "shirt_number",
    "squadnumber",
    "squad_number",
    "maglia",
    "numero",
    "num",
    "no",
    "#",
)

_NAME_KEYS = (
    "name",
    "player",
    "playername",
    "player_name",
    "fullname",
    "nome",
    "display_name",
    "displayname",
)

_POS_KEYS = ("position", "pos", "ruolo")

_HOME_LIST_KEYS = (
    "home",
    "home_players",
    "homeplayers",
    "home_squad",
    "homesquad",
    "squadra_casa",
)

_AWAY_LIST_KEYS = (
    "away",
    "away_players",
    "awayplayers",
    "away_squad",
    "awaysquad",
    "squadra_ospite",
)

_CSV_SIDE_ALIASES = ("side", "team", "squadra", "club")
_CSV_JERSEY_ALIASES = ("jersey", "number", "shirt", "maglia", "numero", "num", "no")
_CSV_NAME_ALIASES = ("name", "player", "player_name", "fullname", "nome", "giocatore")
_CSV_POS_ALIASES = ("position", "pos", "ruolo")


def _clean_name(raw: object) -> str:
    return " ".join(str(raw or "").strip().split())[:80]


def _clean_pos(raw: object) -> str:
    return str(raw or "").strip().upper()[:8]


def _parse_jersey(raw: object) -> int | None:
    """Accept ints, numeric strings, and spreadsheet floats like ``1.0``."""

    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer():
            return None
        value = int(raw)
    elif isinstance(raw, int):
        value = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        # Strip shirt decorations: "#7", "No. 10", "10."
        cleaned = text.replace("#", "").replace("No.", "").replace("no.", "").strip()
        try:
            if "." in cleaned:
                as_float = float(cleaned)
                if not as_float.is_integer():
                    return None
                value = int(as_float)
            else:
                value = int(cleaned)
        except (TypeError, ValueError):
            return None
    if 1 <= value <= 99:
        return value
    return None


def _row_get(row: dict[str, Any], *keys: str) -> object:
    lower = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    return None


def _player_name_from_row(row: dict[str, Any]) -> str:
    direct = _clean_name(_row_get(row, *_NAME_KEYS) or "")
    if direct:
        return direct
    first = _clean_name(
        _row_get(row, "firstname", "first_name", "first", "nome", "givenname", "given_name") or ""
    )
    last = _clean_name(
        _row_get(
            row,
            "lastname",
            "last_name",
            "last",
            "cognome",
            "surname",
            "familyname",
            "family_name",
        )
        or ""
    )
    return _clean_name(f"{first} {last}".strip())


def _player_from_row(row: dict[str, Any], *, side: str) -> LineupPlayer | None:
    jersey = _parse_jersey(_row_get(row, *_JERSEY_KEYS))
    name = _player_name_from_row(row)
    if jersey is None or not name:
        return None
    position = _clean_pos(_row_get(row, *_POS_KEYS) or "")
    return LineupPlayer(side=side, jersey=jersey, name=name, position=position)


def _normalize_side(raw: object) -> str | None:
    text = str(raw or "").strip().lower()
    return _SIDE_ALIASES.get(text)


def _list_from_payload(payload: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    lower = {str(key).strip().lower(): value for key, value in payload.items()}
    for key in keys:
        rows = lower.get(key.lower())
        if isinstance(rows, list):
            return rows
    return []


def _players_from_side_rows(rows: list[Any], *, side: str) -> list[LineupPlayer]:
    out: list[LineupPlayer] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        player = _player_from_row(row, side=side)
        if player is not None:
            out.append(player)
    return out


def _players_from_mixed_rows(rows: list[Any]) -> tuple[list[LineupPlayer], list[LineupPlayer]]:
    home: list[LineupPlayer] = []
    away: list[LineupPlayer] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = _normalize_side(_row_get(row, "side", "team", "squadra", "club"))
        if side is None:
            continue
        player = _player_from_row(row, side=side)
        if player is None:
            continue
        (home if side == "home" else away).append(player)
    return home, away


def _unwrap_json_payload(raw: str | bytes | dict[str, Any] | list[Any]) -> object:
    """Load JSON and unwrap a double-encoded string once."""

    if isinstance(raw, (dict, list)):
        payload: object = raw
    else:
        payload = json.loads(raw)
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{") or text.startswith("["):
            payload = json.loads(text)
    return payload


def _csv_field_map(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames:
        return {}
    return {name.strip().lower(): name for name in fieldnames if name and str(name).strip()}


def _csv_pick(fields: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in fields:
            return fields[alias]
    return None


def parse_lineup_csv(raw: str, *, home_team: str = "Home", away_team: str = "Away") -> MatchLineups:
    """Parse CSV with columns: side, jersey, name [, position].

    Also accepts common aliases (team/squadra, maglia/numero, giocatore/nome)
    and semicolon-delimited European spreadsheet exports.
    """

    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(raw), dialect=dialect)
    fields = _csv_field_map(list(reader.fieldnames or []))
    side_key = _csv_pick(fields, _CSV_SIDE_ALIASES)
    jersey_key = _csv_pick(fields, _CSV_JERSEY_ALIASES)
    name_key = _csv_pick(fields, _CSV_NAME_ALIASES)
    if not side_key or not jersey_key or not name_key:
        raise ValueError("Line-up CSV must include columns: side, jersey, name.")
    pos_key = _csv_pick(fields, _CSV_POS_ALIASES)
    home: list[LineupPlayer] = []
    away: list[LineupPlayer] = []
    for row in reader:
        side = _normalize_side(row.get(side_key, ""))
        if side is None:
            continue
        jersey = _parse_jersey(row.get(jersey_key))
        name = _clean_name(row.get(name_key, ""))
        if jersey is None or not name:
            # Try first+last when a single name column is missing.
            if jersey is not None and not name:
                rebuilt = {
                    str(k).lower(): v
                    for k, v in row.items()
                    if k is not None
                }
                name = _player_name_from_row(rebuilt)
            if jersey is None or not name:
                continue
        position = _clean_pos(row.get(pos_key, "")) if pos_key else ""
        player = LineupPlayer(side=side, jersey=jersey, name=name, position=position)
        (home if side == "home" else away).append(player)
    if not home and not away:
        raise ValueError("Line-up CSV had no usable player rows.")
    return MatchLineups(
        home_team=home_team or "Home",
        away_team=away_team or "Away",
        home=tuple(home),
        away=tuple(away),
    )


def parse_lineup_json(raw: str | bytes | dict[str, Any] | list[Any]) -> MatchLineups:
    """Parse common Home/Away line-up JSON shapes into :class:`MatchLineups`.

    Accepted shapes include:
    - ``{home_team, away_team, home:[{jersey,name}], away:[...]}``
    - ``home_players`` / ``away_players``, nested ``lineups``, or ``teams: [...]``
    - a flat ``players`` / top-level array of ``{side,jersey,name}`` rows
    - spreadsheet exports with float jerseys (``1.0``) and Italian keys
    """

    payload = _unwrap_json_payload(raw)

    home: list[LineupPlayer] = []
    away: list[LineupPlayer] = []
    home_team = "Home"
    away_team = "Away"

    if isinstance(payload, list):
        home, away = _players_from_mixed_rows(payload)
    elif isinstance(payload, dict):
        # Nested under lineups / lineup / data.
        nested = payload.get("lineups") or payload.get("lineup") or payload.get("data")
        if isinstance(nested, dict):
            base = dict(payload)
            base.update(nested)
            payload = base
        assert isinstance(payload, dict)

        home_team = (
            _clean_name(
                payload.get("home_team")
                or payload.get("homeTeam")
                or payload.get("home_name")
                or payload.get("squadra_casa")
                or "Home"
            )
            or "Home"
        )
        away_team = (
            _clean_name(
                payload.get("away_team")
                or payload.get("awayTeam")
                or payload.get("away_name")
                or payload.get("squadra_ospite")
                or "Away"
            )
            or "Away"
        )

        home = _players_from_side_rows(_list_from_payload(payload, _HOME_LIST_KEYS), side="home")
        away = _players_from_side_rows(_list_from_payload(payload, _AWAY_LIST_KEYS), side="away")

        if not home and not away:
            mixed = payload.get("players") or payload.get("squad") or payload.get("roster")
            if isinstance(mixed, list):
                home, away = _players_from_mixed_rows(mixed)

        if not home and not away:
            teams = payload.get("teams")
            if isinstance(teams, list) and teams:
                for index, team in enumerate(teams[:2]):
                    if not isinstance(team, dict):
                        continue
                    side = "home" if index == 0 else "away"
                    team_name = _clean_name(
                        team.get("name") or team.get("team") or team.get("team_name") or ""
                    )
                    if team_name:
                        if side == "home":
                            home_team = team_name
                        else:
                            away_team = team_name
                    rows = team.get("players") or team.get("squad") or team.get("roster") or []
                    if isinstance(rows, list):
                        parsed = _players_from_side_rows(rows, side=side)
                        if side == "home":
                            home = parsed
                        else:
                            away = parsed
    else:
        raise ValueError("Line-up JSON must be an object or an array of player rows.")

    if not home and not away:
        raise ValueError("Line-up JSON had no usable players.")
    return MatchLineups(
        home_team=home_team, away_team=away_team, home=tuple(home), away=tuple(away)
    )


def parse_lineup_bytes(
    data: bytes,
    *,
    filename: str = "",
    home_team: str = "Home",
    away_team: str = "Away",
) -> MatchLineups:
    """Auto-detect CSV vs JSON from filename / content."""

    name = filename.lower()
    text = data.decode("utf-8-sig")
    stripped = text.lstrip()
    looks_json = name.endswith(".json") or stripped.startswith("{") or stripped.startswith("[")

    def _with_ui_names(lineups: MatchLineups) -> MatchLineups:
        return MatchLineups(
            home_team=home_team if home_team and home_team != "Home" else lineups.home_team,
            away_team=away_team if away_team and away_team != "Away" else lineups.away_team,
            home=lineups.home,
            away=lineups.away,
        )

    if looks_json:
        try:
            return _with_ui_names(parse_lineup_json(text))
        except ValueError:
            # Mislabelled CSV / spreadsheet export saved as .json.
            if "side" in stripped.lower() or ";" in stripped[:200] or "," in stripped[:200]:
                try:
                    return _with_ui_names(
                        parse_lineup_csv(text, home_team=home_team, away_team=away_team)
                    )
                except ValueError:
                    pass
            raise
    return parse_lineup_csv(text, home_team=home_team, away_team=away_team)


def example_lineup_csv() -> str:
    """Sample CSV operators can download and fill."""

    return (
        "side,jersey,name,position\n"
        "home,1,Neri,GK\n"
        "home,8,Verdi,CM\n"
        "home,9,Rossi,ST\n"
        "away,1,Blu,GK\n"
        "away,6,Gialli,CB\n"
        "away,10,Bianchi,ST\n"
    )
