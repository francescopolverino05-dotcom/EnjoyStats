"""Match line-ups: real names + shirt numbers for film collect."""

from __future__ import annotations

import csv
import json
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


def _clean_name(raw: str) -> str:
    return " ".join(str(raw or "").strip().split())[:80]


def _clean_pos(raw: str) -> str:
    return str(raw or "").strip().upper()[:8]


def _parse_jersey(raw: object) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= value <= 99:
        return value
    return None


def parse_lineup_csv(raw: str, *, home_team: str = "Home", away_team: str = "Away") -> MatchLineups:
    """Parse CSV with columns: side, jersey, name [, position]."""

    reader = csv.DictReader(StringIO(raw))
    if not reader.fieldnames:
        raise ValueError("Line-up CSV needs a header row (side,jersey,name).")
    fields = {name.strip().lower(): name for name in reader.fieldnames if name}
    required = {"side", "jersey", "name"}
    if not required.issubset(fields):
        raise ValueError("Line-up CSV must include columns: side, jersey, name.")
    home: list[LineupPlayer] = []
    away: list[LineupPlayer] = []
    for row in reader:
        side_raw = str(row.get(fields["side"], "")).strip().lower()
        if side_raw in {"h", "home", "1"}:
            side = "home"
        elif side_raw in {"a", "away", "2"}:
            side = "away"
        else:
            continue
        jersey = _parse_jersey(row.get(fields["jersey"]))
        name = _clean_name(row.get(fields["name"], ""))
        if jersey is None or not name:
            continue
        pos_key = fields.get("position")
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


def parse_lineup_json(raw: str | bytes | dict[str, Any]) -> MatchLineups:
    """Parse JSON ``{home_team, away_team, home:[{jersey,name,position}], away:[...]}``."""

    if isinstance(raw, (str, bytes)):
        payload = json.loads(raw)
    else:
        payload = raw
    if not isinstance(payload, dict):
        raise ValueError("Line-up JSON must be an object.")
    home_team = _clean_name(payload.get("home_team") or payload.get("homeTeam") or "Home") or "Home"
    away_team = _clean_name(payload.get("away_team") or payload.get("awayTeam") or "Away") or "Away"

    def _side(key: str, side: str) -> list[LineupPlayer]:
        rows = payload.get(key) or payload.get(key.capitalize()) or []
        if not isinstance(rows, list):
            return []
        out: list[LineupPlayer] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            jersey = _parse_jersey(row.get("jersey") or row.get("number") or row.get("shirt"))
            name = _clean_name(row.get("name") or row.get("player") or "")
            if jersey is None or not name:
                continue
            position = _clean_pos(row.get("position") or row.get("pos") or "")
            out.append(LineupPlayer(side=side, jersey=jersey, name=name, position=position))
        return out

    home = _side("home", "home")
    away = _side("away", "away")
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
    if name.endswith(".json") or text.lstrip().startswith("{"):
        lineups = parse_lineup_json(text)
        # Allow UI team names to override empty defaults.
        return MatchLineups(
            home_team=home_team if home_team and home_team != "Home" else lineups.home_team,
            away_team=away_team if away_team and away_team != "Away" else lineups.away_team,
            home=lineups.home,
            away=lineups.away,
        )
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
