"""StatMan IQ — tactical glossary + classifiers for match film auto-tag.

Sources (simplified wording for operators and CV rules) — see ``IQ_SOURCES``.

Event vocabulary is aligned in :mod:`analytics.event_registry`. Film CV is
still geometry — StatMan IQ is the football brain that stops nonsense like
79–10 scorelines and keeper clearances counted as shots.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from analytics.event_registry import is_wyscout_danger_zone
from data_models.events import EventType, MatchEvent, ShotOutcome

# Operator-fed sources (ingested into glossary + classifiers).
IQ_SOURCES: Final[tuple[tuple[str, str], ...]] = (
    ("Wyscout Data Glossary", "https://dataglossary.wyscout.com/"),
    ("Wyscout Shot", "https://dataglossary.wyscout.com/shot/"),
    ("Wyscout Pass", "https://dataglossary.wyscout.com/pass/"),
    ("Wyscout Cross", "https://dataglossary.wyscout.com/cross/"),
    ("Opta Event Definitions", "https://www.statsperform.com/opta-event-definitions/"),
    ("StatsBomb Open Data", "https://github.com/statsbomb/open-data"),
    (
        "StatsBomb Events Spec v4",
        "https://github.com/statsbomb/open-data/blob/master/doc/Open%20Data%20Events%20v4.0.0.pdf",
    ),
    ("Spielverlagerung (tactics)", "https://spielverlagerung.com/"),
    ("Coaches' Voice", "https://www.coachesvoice.com/"),
)

# --- Shot / goal gates (football, not NBA) ---------------------------------
SHOT_MIN_GAP_S: Final[float] = 12.0
GOAL_MIN_GAP_S: Final[float] = 60.0
SHOT_MIN_TRAVEL: Final[float] = 14.0
SHOT_MIN_SPEED: Final[float] = 24.0
GOAL_MOUTH_X: Final[float] = 3.0
GOAL_POST_Y_MIN: Final[float] = 38.0
GOAL_POST_Y_MAX: Final[float] = 62.0
# Sparse film needs a longer window to link shot → mouth.
PENDING_SHOT_TTL_S: Final[float] = 5.0
# Film CV hard cap — real matches almost never need more; 20 goals was noise.
DEFAULT_MAX_FILM_GOALS: Final[int] = 8


def max_film_goals() -> int:
    raw = os.environ.get("STATMAN_MAX_GOALS", "").strip()
    if not raw:
        return DEFAULT_MAX_FILM_GOALS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MAX_FILM_GOALS


def sanitize_film_goals(events: list[MatchEvent]) -> list[MatchEvent]:
    """Demote excess film goals to shots (Opta: goal = deliberate attempt that scores).

    Sparse tracking invents dozens of mouth hits; keep the earliest goals only.
    """

    limit = max_film_goals()
    if limit <= 0:
        return events
    goal_idxs = [
        i
        for i, event in enumerate(events)
        if event.event_type is EventType.GOAL or event.is_goal
    ]
    if len(goal_idxs) <= limit:
        return events
    drop = set(goal_idxs[limit:])
    cleaned: list[MatchEvent] = []
    for i, event in enumerate(events):
        if i not in drop:
            cleaned.append(event)
            continue
        cleaned.append(
            event.model_copy(
                update={
                    "event_type": EventType.SHOT,
                    "is_goal": False,
                    "shot_outcome": event.shot_outcome or ShotOutcome.ON_TARGET,
                }
            )
        )
    return cleaned

# Half-spaces (Halbraum): between wing and centre — Spielverlagerung / CV.
HALF_SPACE_Y_LO: Final[tuple[float, float]] = (20.0, 40.0)
HALF_SPACE_Y_HI: Final[tuple[float, float]] = (60.0, 80.0)
WING_Y_LO: Final[float] = 18.0
WING_Y_HI: Final[float] = 82.0
# Wyscout cross flanks: pitch width in thirds (~68m → leftmost / rightmost third).
CROSS_FLANK_Y_LO: Final[float] = 33.8
CROSS_FLANK_Y_HI: Final[float] = 66.2


class GamePhase(StrEnum):
    """Four game phases (JMftbl / Coaches' Voice)."""

    ATTACKING_ORGANISATION = "attacking_organisation"
    DEFENSIVE_ORGANISATION = "defensive_organisation"
    ATTACKING_TRANSITION = "attacking_transition"
    DEFENSIVE_TRANSITION = "defensive_transition"


class PitchLane(StrEnum):
    """Width lane on the attacking frame."""

    LEFT_WING = "left_wing"
    LEFT_HALF_SPACE = "left_half_space"
    CENTRE = "centre"
    RIGHT_HALF_SPACE = "right_half_space"
    RIGHT_WING = "right_wing"


class DefensiveBlock(StrEnum):
    """How high the defending line sits."""

    LOW = "low_block"
    MID = "mid_block"
    HIGH = "high_block"


# Operator-facing glossary (short definitions — skim before trusting CV tags).
GLOSSARY: dict[str, str] = {
    "gk_rule": (
        "Goalkeepers do not take shots or score in open play on film tags. "
        "A keeper touch in their own third is a pass, clearance, or save — "
        "never a shot or goal."
    ),
    "attacking_organisation": "Your team has the ball and is set in its attacking shape.",
    "defensive_organisation": "The opponent has the ball and you are set defensively.",
    "attacking_transition": "The seconds right after you win the ball.",
    "defensive_transition": "The seconds right after you lose the ball.",
    "rest_defence": (
        "Players left behind the ball while you attack, ready to stop a counter "
        "(German: Restverteidigung)."
    ),
    "half_space": (
        "The lane between the wing and the centre. Good place to receive and "
        "progress (German: Halbraum)."
    ),
    "low_block": "Defensive line near your own box.",
    "mid_block": "Defensive line between your box and the centre circle.",
    "high_block": "Defensive line near halfway.",
    "counterpress": "Press right after losing the ball to win it back quickly.",
    "free_man": "An unmarked teammate created by good spacing.",
    "third_man": "The third player in a pass triangle — often the free man.",
    "cover_shadow": "Space a defender blocks behind them relative to the ball.",
    "depth": "Space behind the opponent’s last line.",
    "switch_of_play": "Moving the ball quickly from one wing to the other.",
    "cutback": "A pass pulled back from the byline toward the penalty spot / late runners.",
    "shot": (
        "Wyscout: attempt towards the opposition goal with intent to score "
        "(blocked / penalties / direct FK count). On film: real strike in the box "
        "or danger zone — not a dribble. Keepers never shoot."
    ),
    "goal": (
        "Wyscout: shot with Goal=Yes. On film: only after a shot reaches the mouth, "
        "then capped to the distinti scoreline (extra film goals → shots)."
    ),
    "pass": (
        "Wyscout: attempt to pass to a teammate; successful if next touch is a "
        "teammate. Excludes throw-ins / corner crosses / FK crosses from pass totals. "
        "Opta counts crosses separately from pass totals."
    ),
    "cross": (
        "Wyscout: open-play ball from the offensive flanks (outer thirds of width) "
        "aimed at a teammate in front of the opponent goal — not corners/FK."
    ),
    "interception": "Cutting out a pass by reading the lane (Opta/StatsBomb).",
    "ball_recovery": (
        "First touch starting your possession after winning the ball in open play "
        "(Wyscout recovery / Opta ball recovery)."
    ),
    "danger_zone": ("Wyscout central shooting band (x≥84.29, y 36.29–63.71 on 0–100 pitch)."),
    "distinti_score": (
        "Official home–away goals from the match sheet are law for film Analyse. "
        "Never let every shot near the net become a goal."
    ),
}


@dataclass(frozen=True, slots=True)
class StrikeVerdict:
    """Whether geometry looks like a shot and/or a goal."""

    is_shot: bool
    is_goal: bool
    on_target: bool
    reason: str


def pitch_lane(y: float) -> PitchLane:
    """Map pitch width (0–100) onto wing / half-space / centre."""

    if y <= WING_Y_LO:
        return PitchLane.LEFT_WING
    if HALF_SPACE_Y_LO[0] <= y < HALF_SPACE_Y_LO[1]:
        return PitchLane.LEFT_HALF_SPACE
    if HALF_SPACE_Y_HI[0] < y <= HALF_SPACE_Y_HI[1]:
        return PitchLane.RIGHT_HALF_SPACE
    if y >= WING_Y_HI:
        return PitchLane.RIGHT_WING
    return PitchLane.CENTRE


def is_half_space(y: float) -> bool:
    lane = pitch_lane(y)
    return lane in {PitchLane.LEFT_HALF_SPACE, PitchLane.RIGHT_HALF_SPACE}


def is_wing(y: float) -> bool:
    return pitch_lane(y) in {PitchLane.LEFT_WING, PitchLane.RIGHT_WING}


def is_cross_flank(y: float) -> bool:
    """Wyscout flank thirds for open-play crosses (0–100 pitch width)."""

    return y <= CROSS_FLANK_Y_LO or y >= CROSS_FLANK_Y_HI


def defensive_block_for_line(
    defensive_line_x: float, *, defending_left_goal: bool
) -> DefensiveBlock:
    """Classify block height from the deepest outfield defensive line x."""

    # Normalize so "depth from own goal" rises as the line pushes up.
    depth = defensive_line_x if defending_left_goal else 100.0 - defensive_line_x
    if depth <= 22.0:
        return DefensiveBlock.LOW
    if depth <= 45.0:
        return DefensiveBlock.MID
    return DefensiveBlock.HIGH


def is_goalkeeper_actor(position: str | None, *, player_name: str | None = None) -> bool:
    """True when the actor should follow the GK rule (no shots/goals)."""

    pos = (position or "").strip().upper()
    if pos in {"GK", "GKP", "GOALKEEPER", "PORTIERE"}:
        return True
    name = (player_name or "").casefold()
    return " gk " in f" {name} " or name.endswith(" gk") or "goalkeeper" in name


def own_defensive_third(x: float, attack_goal_x: float) -> bool:
    """Ball in the actor’s own defensive third (build-out / rest-defence zone)."""

    if attack_goal_x >= 50:
        return x <= 33.0
    return x >= 67.0


def classify_strike(
    *,
    start: tuple[float, float],
    point: tuple[float, float],
    travel: float,
    speed: float,
    attack_goal_x: float,
    shot_gap_ok: bool,
    goal_gap_ok: bool,
    gap_ok: bool,
    actor_is_gk: bool,
    pending_shot: bool,
    pending_fresh: bool,
) -> StrikeVerdict:
    """Decide shot/goal with StatMan IQ — GK rule first, then strike + mouth."""

    if actor_is_gk:
        # Opta/Wyscout: keeper distribution and clearances are never shots.
        return StrikeVerdict(False, False, False, "gk_rule")

    toward_goal = abs(point[0] - attack_goal_x) < abs(start[0] - attack_goal_x)
    box_x = 82.0 if attack_goal_x >= 50 else 18.0
    in_box = point[0] >= box_x if attack_goal_x >= 50 else point[0] <= box_x
    start_in_box = start[0] >= box_x if attack_goal_x >= 50 else start[0] <= box_x
    in_danger = is_wyscout_danger_zone(point[0], point[1], attack_goal_x=attack_goal_x)
    start_in_danger = is_wyscout_danger_zone(start[0], start[1], attack_goal_x=attack_goal_x)
    between_posts = GOAL_POST_Y_MIN <= point[1] <= GOAL_POST_Y_MAX
    at_mouth = abs(point[0] - attack_goal_x) <= GOAL_MOUTH_X and between_posts
    wide = is_wing(point[1]) or is_wing(start[1])
    strike = travel >= SHOT_MIN_TRAVEL or speed >= SHOT_MIN_SPEED
    # Wyscout: scoring intent in box or danger zone; Opta: deliberate attempt on target.
    shooting_zone = in_box or start_in_box or in_danger or start_in_danger
    shot_like = (
        toward_goal
        and shooting_zone
        and strike
        and not wide
        and shot_gap_ok
        and (gap_ok or at_mouth)
    )

    # Goals only after a prior shot tag reaches the mouth. Instant
    # "strike_at_mouth" produced 20-goal nonsense on sparse Railway film.
    if pending_shot and at_mouth and toward_goal and goal_gap_ok and pending_fresh:
        return StrikeVerdict(True, True, True, "pending_shot_to_mouth")
    if shot_like and at_mouth and goal_gap_ok:
        # Count as a shot on target — not a goal — unless we already had a shot.
        return StrikeVerdict(True, False, True, "strike_at_mouth_shot")
    if shot_like:
        on_target = between_posts and abs(point[0] - attack_goal_x) <= 12.0
        return StrikeVerdict(True, False, on_target, "strike")
    return StrikeVerdict(False, False, False, "not_a_shot")


def classify_distribution(
    *,
    start: tuple[float, float],
    point: tuple[float, float],
    travel: float,
    toward_goal: bool,
    in_box: bool,
    touchline: bool,
    corner: bool,
    actor_is_gk: bool,
) -> EventType:
    """Pick pass / cross / cutback / throw-in / corner with lane IQ."""

    if corner:
        return EventType.CORNER
    if touchline and travel < 12.0:
        return EventType.THROW_IN

    start_lane = pitch_lane(start[1])
    from_byline = start[0] >= 90.0 or start[0] <= 10.0
    pulling_back = (not toward_goal) and in_box and travel >= 8.0
    if (
        from_byline
        and pulling_back
        and start_lane
        in {
            PitchLane.LEFT_WING,
            PitchLane.RIGHT_WING,
            PitchLane.LEFT_HALF_SPACE,
            PitchLane.RIGHT_HALF_SPACE,
        }
    ):
        return EventType.CUTBACK

    # Wyscout Cross: open-play delivery from offensive flanks into the box.
    if (
        is_cross_flank(start[1])
        and in_box
        and toward_goal
        and travel >= 10.0
        and not actor_is_gk
    ):
        return EventType.CROSS

    # StatsBomb clearance / Wyscout loss point: GK hoof in own third stays a pass.
    return EventType.PASS


def shot_outcome_for(verdict: StrikeVerdict) -> ShotOutcome:
    if verdict.is_goal or verdict.on_target:
        return ShotOutcome.ON_TARGET
    return ShotOutcome.MISSED


def progressive_from_half_space(
    *,
    start_y: float,
    toward_goal: bool,
    travel: float,
) -> bool:
    """Half-space reception that advances — classic progression pattern."""

    return is_half_space(start_y) and toward_goal and travel >= 10.0
