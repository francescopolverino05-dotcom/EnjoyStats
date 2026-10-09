"""Parse Italian match sheets (distinti / tabellini PDF) for official facts.

Operators upload the distinti PDF with the line-up CSV. We extract:
- team names
- final score (home–away)
- shirt numbers + names when the PDF is text-based

Scanned image-only PDFs need OCR later; text PDFs from FIGC/Lega work today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from analytics.lineups import LineupPlayer, MatchLineups, _clean_name, _parse_jersey


@dataclass(frozen=True, slots=True)
class MatchFacts:
    """Official match facts from a distinti / tabellino."""

    home_team: str
    away_team: str
    home_goals: int | None
    away_goals: int | None
    home: tuple[LineupPlayer, ...]
    away: tuple[LineupPlayer, ...]
    scorers_home: tuple[str, ...] = ()
    scorers_away: tuple[str, ...] = ()
    source_label: str = "distinti"

    @property
    def lineups(self) -> MatchLineups | None:
        if not self.home and not self.away:
            return None
        return MatchLineups(
            home_team=self.home_team or "Home",
            away_team=self.away_team or "Away",
            home=self.home,
            away=self.away,
        )

    def score_label(self) -> str:
        if self.home_goals is None or self.away_goals is None:
            return "score unknown"
        return f"{self.home_goals}-{self.away_goals}"


_SCORE_PATTERNS = (
    re.compile(
        r"(?:risultato|finale|ft|score)\s*[:\-]?\s*(\d{1,2})\s*[-–:]\s*(\d{1,2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(?:\(|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'\-]{2,40})\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\s+"
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'\-]{2,40})",
        re.IGNORECASE,
    ),
)

# "10 ROSSI Mario" / "10. Rossi" / "#10 Rossi"
_PLAYER_LINE = re.compile(
    r"^\s*#?(\d{1,2})[.)\-\s]+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'\-]{1,60})\s*$"
)

_HOME_MARKERS = (
    "squadra casa",
    "squadra di casa",
    "home",
    "titolari casa",
    "formazione casa",
    "casa:",
)
_AWAY_MARKERS = (
    "squadra ospite",
    "ospiti",
    "away",
    "titolari ospite",
    "formazione ospite",
    "trasferta",
    "ospite:",
)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover — dependency required in image
        raise ValueError(
            "PDF support needs the pypdf package. Redeploy Web after updating requirements."
        ) from exc
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not read distinti PDF ({exc}).") from exc
    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    text = "\n".join(chunks)
    if len(text.strip()) < 40:
        raise ValueError(
            "Distinti PDF has almost no text (likely a scan). "
            "Export a text PDF or paste the tabellino as CSV/JSON for now."
        )
    return text


def _extract_score(text: str) -> tuple[int | None, int | None, str, str]:
    home_team = ""
    away_team = ""
    for pattern in _SCORE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 2:
            return int(groups[0]), int(groups[1]), home_team, away_team
        if len(groups) == 4:
            return (
                int(groups[1]),
                int(groups[2]),
                _clean_name(groups[0]),
                _clean_name(groups[3]),
            )
    # Bare score near top of document.
    head = "\n".join(text.splitlines()[:40])
    bare = re.search(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b", head)
    if bare:
        return int(bare.group(1)), int(bare.group(2)), home_team, away_team
    return None, None, home_team, away_team


def _side_from_context(line: str, current: str) -> str:
    lower = line.casefold()
    if any(marker in lower for marker in _HOME_MARKERS):
        return "home"
    if any(marker in lower for marker in _AWAY_MARKERS):
        return "away"
    return current


def _extract_players(text: str) -> tuple[list[LineupPlayer], list[LineupPlayer]]:
    home: list[LineupPlayer] = []
    away: list[LineupPlayer] = []
    side = "home"
    seen: set[tuple[str, int]] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        side = _side_from_context(line, side)
        match = _PLAYER_LINE.match(line)
        if match is None:
            continue
        jersey = _parse_jersey(match.group(1))
        name = _clean_name(match.group(2))
        if jersey is None or not name:
            continue
        # Skip score-like leftovers.
        if name.isdigit():
            continue
        key = (side, jersey)
        if key in seen:
            continue
        seen.add(key)
        player = LineupPlayer(side=side, jersey=jersey, name=name, position="")
        (home if side == "home" else away).append(player)
    return home, away


def parse_distinti_text(text: str, *, source_label: str = "distinti") -> MatchFacts:
    """Parse plain text extracted from a distinti / tabellino."""

    home_goals, away_goals, home_team, away_team = _extract_score(text)
    home, away = _extract_players(text)
    if not home_team and home:
        home_team = "Home"
    if not away_team and away:
        away_team = "Away"
    if home_goals is None and away_goals is None and not home and not away:
        raise ValueError(
            "Could not find a score or line-up in the distinti. "
            "Check it is a text PDF (not only a photo)."
        )
    return MatchFacts(
        home_team=home_team or "Home",
        away_team=away_team or "Away",
        home_goals=home_goals,
        away_goals=away_goals,
        home=tuple(home),
        away=tuple(away),
        source_label=source_label,
    )


def parse_distinti_pdf(data: bytes, *, filename: str = "") -> MatchFacts:
    """Extract match facts from a distinti PDF upload."""

    label = filename.strip() or "distinti.pdf"
    text = _pdf_text(data)
    return parse_distinti_text(text, source_label=label)


def facts_to_payload(facts: MatchFacts) -> dict[str, Any]:
    """JSON fragment embedded in ``lineup_json`` for the collect worker."""

    return {
        "home_team": facts.home_team,
        "away_team": facts.away_team,
        "home_goals": facts.home_goals,
        "away_goals": facts.away_goals,
        "source": facts.source_label,
        "home": [
            {"jersey": p.jersey, "name": p.name, "position": p.position} for p in facts.home
        ],
        "away": [
            {"jersey": p.jersey, "name": p.name, "position": p.position} for p in facts.away
        ],
    }


def merge_lineups_with_facts(
    lineups: MatchLineups | None,
    facts: MatchFacts | None,
) -> MatchLineups | None:
    """Prefer CSV/JSON jerseys when present; fill gaps from distinti."""

    if facts is None:
        return lineups
    if lineups is None:
        return facts.lineups
    home = list(lineups.home) or list(facts.home)
    away = list(lineups.away) or list(facts.away)
    return MatchLineups(
        home_team=lineups.home_team
        if lineups.home_team and lineups.home_team != "Home"
        else (facts.home_team or lineups.home_team),
        away_team=lineups.away_team
        if lineups.away_team and lineups.away_team != "Away"
        else (facts.away_team or lineups.away_team),
        home=tuple(home),
        away=tuple(away),
    )


def apply_official_score(
    events: list[Any],
    *,
    home_team_id: Any,
    away_team_id: Any,
    home_goals: int,
    away_goals: int,
) -> list[Any]:
    """Distinti scoreline is law: keep ≤ official goals per side, else → shot.

    Film CV often tags every strike near the mouth as a goal. When the
    distinti says 0–4, home keeps 0 goals and away keeps at most 4; every
    extra film “goal” becomes a shot on target.

    ``events`` are :class:`~data_models.events.MatchEvent` instances.
    """

    from data_models.events import EventType, ShotOutcome

    home_goals = max(0, int(home_goals))
    away_goals = max(0, int(away_goals))
    kept_home = 0
    kept_away = 0
    out: list[Any] = []
    for event in events:
        is_goal = bool(getattr(event, "is_goal", False)) or (
            getattr(event, "event_type", None) is EventType.GOAL
        )
        if not is_goal:
            out.append(event)
            continue
        team_id = getattr(event, "team_id", None)
        if team_id == home_team_id:
            if kept_home < home_goals:
                kept_home += 1
                out.append(event)
            else:
                out.append(
                    event.model_copy(
                        update={
                            "event_type": EventType.SHOT,
                            "is_goal": False,
                            "shot_outcome": getattr(event, "shot_outcome", None)
                            or ShotOutcome.ON_TARGET,
                        }
                    )
                )
        elif team_id == away_team_id:
            if kept_away < away_goals:
                kept_away += 1
                out.append(event)
            else:
                out.append(
                    event.model_copy(
                        update={
                            "event_type": EventType.SHOT,
                            "is_goal": False,
                            "shot_outcome": getattr(event, "shot_outcome", None)
                            or ShotOutcome.ON_TARGET,
                        }
                    )
                )
        else:
            # Unknown team — demote to be safe.
            out.append(
                event.model_copy(
                    update={
                        "event_type": EventType.SHOT,
                        "is_goal": False,
                        "shot_outcome": getattr(event, "shot_outcome", None)
                        or ShotOutcome.ON_TARGET,
                    }
                )
            )
    return out
