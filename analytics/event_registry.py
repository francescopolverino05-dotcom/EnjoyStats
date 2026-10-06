"""Industry event definitions aligned to EnjoyStats ``EventType``.

Sources (simplified operator wording):
- Wyscout Data Glossary — https://dataglossary.wyscout.com/
- Opta Event Definitions — https://www.statsperform.com/opta-event-definitions/
- StatsBomb Open Data — https://github.com/statsbomb/open-data
- Coaches' Voice tactics glossary
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from data_models.events import EventType

# Wyscout shot danger zone on 0–100 pitch (dataglossary.wyscout.com/shot/)
WYSCOUT_DANGER_X_MIN: Final[float] = 84.29
WYSCOUT_DANGER_Y_MIN: Final[float] = 36.29
WYSCOUT_DANGER_Y_MAX: Final[float] = 63.71


class DataProvider(StrEnum):
    """External data models referenced by EnjoyStats IQ."""

    WYSCOUT = "wyscout"
    OPTA = "opta"
    STATSBOMB = "statsbomb"
    COACHES_VOICE = "coaches_voice"
    ENJOYSTATS = "enjoystats"


@dataclass(frozen=True, slots=True)
class EventDefinition:
    """One EnjoyStats event mapped to provider vocabulary."""

    event_type: EventType
    summary: str
    wyscout: str = ""
    opta: str = ""
    statsbomb: str = ""
    coaches_voice: str = ""
    film_note: str = ""


EVENT_REGISTRY: dict[EventType, EventDefinition] = {
    EventType.PASS: EventDefinition(
        event_type=EventType.PASS,
        summary="Ball moved deliberately to a teammate.",
        wyscout="Pass — attempt to pass to a teammate; excludes throw-ins and set-piece crosses.",
        opta="Pass — delivery to a teammate; crosses and throw-ins counted separately.",
        statsbomb="Pass — ball passed between teammates.",
        coaches_voice="Combination / switch of play building blocks.",
        film_note="Default open-play action when not a cross, cutback, or set piece.",
    ),
    EventType.CROSS: EventDefinition(
        event_type=EventType.CROSS,
        summary="Delivery from a flank into the box.",
        wyscout="Cross — from offensive flank into the penalty area; open play only.",
        opta="Cross — wide delivery targeting teammates in front of goal.",
        statsbomb="Pass with cross attribute into the penalty area.",
        coaches_voice="Wide delivery from full-back or winger.",
        film_note="Wing or half-space origin, into the box, toward goal.",
    ),
    EventType.CUTBACK: EventDefinition(
        event_type=EventType.CUTBACK,
        summary="Pull-back from the byline toward the spot / late runners.",
        wyscout="Smart / lateral pass from wide — treated as pass subtype.",
        opta="Pull back — pass inside the penalty area pulled back from the byline.",
        statsbomb="Pass with cut-back / pull-back shape from wide.",
        coaches_voice="Cutback from byline after carrying wide.",
        film_note="Byline to central box, ball moving away from the end line.",
    ),
    EventType.SHOT: EventDefinition(
        event_type=EventType.SHOT,
        summary="Deliberate attempt to score.",
        wyscout=(
            "Shot — attempt toward goal with scoring intent; on target if it would "
            "score but for a save; frame hits are not on target."
        ),
        opta=(
            "Shot on/off target — deliberate attempt; on target includes goals and "
            "keeper saves; blocked last-line deflections count on target."
        ),
        statsbomb="Shot — attempt to score with any legal body part.",
        coaches_voice="Direct threat on goal from open play or set piece.",
        film_note=(
            "Real strike in the box or Wyscout danger zone; not a dribble; "
            "keepers never shoot on film."
        ),
    ),
    EventType.GOAL: EventDefinition(
        event_type=EventType.GOAL,
        summary="Ball enters the net.",
        wyscout="Shot with Goal=Yes.",
        opta="Goal attributed to scorer or own-goal defender.",
        statsbomb="Goal event following a scoring shot.",
        coaches_voice="Finished attack.",
        film_note="Ball at goal mouth between the posts after a strike.",
    ),
    EventType.ASSIST: EventDefinition(
        event_type=EventType.ASSIST,
        summary="Final pass before a goal.",
        wyscout="Assist — last touch before a teammate scores.",
        opta="Assist — final touch leading directly to a goal.",
        statsbomb="Pass with shot_assist / goal_assist flag.",
        coaches_voice="Key pass that becomes a goal.",
        film_note="Derived after goal events in official XML; not inferred on film yet.",
    ),
    EventType.INTERCEPTION: EventDefinition(
        event_type=EventType.INTERCEPTION,
        summary="Reading and cutting out an opponent pass.",
        wyscout="Interception — anticipating pass, shot, or cross; includes shot blocks.",
        opta="Interception — moving into the intended passing lane.",
        statsbomb="Interception — preventing pass reaching a teammate.",
        coaches_voice="Winning the ball by reading the pass.",
        film_note="Emitted on turnover when possession changes in open play.",
    ),
    EventType.BALL_RECOVERY: EventDefinition(
        event_type=EventType.BALL_RECOVERY,
        summary="First controlled touch after winning open-play possession.",
        wyscout=(
            "Recovery — ends opponent possession and starts yours; not after "
            "throw-in/foul restarts."
        ),
        opta="Ball Recovery — possession change in open play, first controlled action.",
        statsbomb="Ball Recovery — attempt to recover a loose ball.",
        coaches_voice="Attacking or defensive transition after winning the ball.",
        film_note="Paired with ball_lost on the same turnover frame.",
    ),
    EventType.BALL_LOST: EventDefinition(
        event_type=EventType.BALL_LOST,
        summary="Possession given away in open play.",
        wyscout="Loss — ends your possession (start point of failed pass or duel).",
        opta="Unsuccessful touch or lost duel ending possession.",
        statsbomb="Dispossessed / miscontrol when possession changes.",
        coaches_voice="Defensive transition trigger for the opponent.",
        film_note="Paired with interception + recovery for the opponent.",
    ),
    EventType.GROUND_DUEL: EventDefinition(
        event_type=EventType.GROUND_DUEL,
        summary="Two players contest the ball on the ground.",
        wyscout="Offensive or defensive duel — below elbow height, paired events.",
        opta="Challenge / tackle / duel for the ball.",
        statsbomb="Duel — ground contest for possession.",
        coaches_voice="1v1 on the floor.",
        film_note="Tight cluster, low travel, opponent within ~9m.",
    ),
    EventType.AERIAL_DUEL: EventDefinition(
        event_type=EventType.AERIAL_DUEL,
        summary="Head or upper-body contest above elbow height.",
        wyscout="Aerial duel — above elbow height, paired per opponent pair.",
        opta="Aerial duel.",
        statsbomb="Duel with aerial type.",
        coaches_voice="Header duel.",
        film_note="Many players clustered in one frame.",
    ),
    EventType.BLOCK_SHOT: EventDefinition(
        event_type=EventType.BLOCK_SHOT,
        summary="Outfield block of a shot attempt.",
        wyscout="Interception when blocking a shot.",
        opta="Blocked Shot — defender blocks attempt still on target to goal.",
        statsbomb="Block — standing in the shot path.",
        coaches_voice="Last-ditch block.",
        film_note="Not inferred on film yet; official XML only.",
    ),
    EventType.CORNER: EventDefinition(
        event_type=EventType.CORNER,
        summary="Corner kick.",
        wyscout="Set piece — not counted as open-play cross.",
        opta="Corner — set-piece pattern before open play resumes.",
        statsbomb="Pass from corner flag.",
        coaches_voice="Dead-ball wide delivery.",
        film_note="Ball near corner arc on film geometry.",
    ),
    EventType.THROW_IN: EventDefinition(
        event_type=EventType.THROW_IN,
        summary="Throw-in restart.",
        wyscout="Not counted as a pass.",
        opta="Throw in — set-piece pattern.",
        statsbomb="Out + pass subtype.",
        coaches_voice="Restart from touchline.",
        film_note="Touchline + short travel.",
    ),
    EventType.FREE_KICK: EventDefinition(
        event_type=EventType.FREE_KICK,
        summary="Free kick restart.",
        wyscout="Set piece.",
        opta="Direct or indirect free kick pattern.",
        statsbomb="Pass / shot from free kick.",
        coaches_voice="Dead-ball restart.",
        film_note="Not inferred on film yet.",
    ),
    EventType.SAVE: EventDefinition(
        event_type=EventType.SAVE,
        summary="Goalkeeper stop of a shot on target.",
        wyscout="Save on targeted shot.",
        opta="Save — keeper prevents on-target attempt entering goal.",
        statsbomb="Goal Keeper save outcome.",
        coaches_voice="Keeper intervention.",
        film_note="Not inferred on film yet; GK rule blocks keeper shots instead.",
    ),
    EventType.GOAL_CONCEDED: EventDefinition(
        event_type=EventType.GOAL_CONCEDED,
        summary="Goal conceded by defending team.",
        wyscout="Goal against.",
        opta="Goal against / own goal against.",
        statsbomb="Goal against team.",
        coaches_voice="Conceded goal.",
        film_note="Official XML / derived from opponent goal.",
    ),
    EventType.OFFSIDE: EventDefinition(
        event_type=EventType.OFFSIDE,
        summary="Offside offence.",
        wyscout="Offside.",
        opta="Offside — deepest defender line at pass.",
        statsbomb="Offside flag.",
        coaches_voice="Offside.",
        film_note="Not inferred on film yet.",
    ),
    EventType.FOUL_COMMITTED: EventDefinition(
        event_type=EventType.FOUL_COMMITTED,
        summary="Foul given against the actor.",
        wyscout="Foul.",
        opta="Foul Conceded — leads to free kick or penalty.",
        statsbomb="Foul Committed.",
        coaches_voice="Foul.",
        film_note="Not inferred on film yet.",
    ),
    EventType.FOUL_WON: EventDefinition(
        event_type=EventType.FOUL_WON,
        summary="Foul won by the actor.",
        wyscout="Foul suffered.",
        opta="Foul Won.",
        statsbomb="Foul Won.",
        coaches_voice="Foul won.",
        film_note="Not inferred on film yet.",
    ),
    EventType.YELLOW_CARD: EventDefinition(
        event_type=EventType.YELLOW_CARD,
        summary="Yellow card.",
        wyscout="Disciplinary.",
        opta="Card.",
        statsbomb="Bad Behaviour card.",
        coaches_voice="Booking.",
        film_note="Official XML only.",
    ),
    EventType.RED_CARD: EventDefinition(
        event_type=EventType.RED_CARD,
        summary="Red card.",
        wyscout="Disciplinary.",
        opta="Card.",
        statsbomb="Bad Behaviour card.",
        coaches_voice="Sending off.",
        film_note="Official XML only.",
    ),
    EventType.BLOCK_CROSS: EventDefinition(
        event_type=EventType.BLOCK_CROSS,
        summary="Block of a cross.",
        wyscout="Cross blocked.",
        opta="Blocked cross.",
        statsbomb="Block.",
        coaches_voice="Cross blocked.",
        film_note="Official XML only.",
    ),
    EventType.BLOCK_PASS: EventDefinition(
        event_type=EventType.BLOCK_PASS,
        summary="Block of a pass.",
        wyscout="Pass blocked.",
        opta="Blocked pass.",
        statsbomb="Block.",
        coaches_voice="Pass blocked.",
        film_note="Official XML only.",
    ),
}


def event_definition(event_type: EventType) -> EventDefinition:
    """Return registry row for ``event_type``."""

    row = EVENT_REGISTRY.get(event_type)
    if row is None:
        return EventDefinition(
            event_type=event_type,
            summary=f"EnjoyStats event `{event_type.value}`.",
        )
    return row


def provider_note(event_type: EventType, provider: DataProvider) -> str:
    """Short provider-specific wording for UI / reports."""

    row = event_definition(event_type)
    if provider is DataProvider.WYSCOUT:
        return row.wyscout or row.summary
    if provider is DataProvider.OPTA:
        return row.opta or row.summary
    if provider is DataProvider.STATSBOMB:
        return row.statsbomb or row.summary
    if provider is DataProvider.COACHES_VOICE:
        return row.coaches_voice or row.summary
    return row.summary


def is_wyscout_danger_zone(x: float, y: float, *, attack_goal_x: float) -> bool:
    """Wyscout danger zone — central final-third shooting area."""

    if attack_goal_x >= 50:
        return (
            x >= WYSCOUT_DANGER_X_MIN
            and WYSCOUT_DANGER_Y_MIN <= y <= WYSCOUT_DANGER_Y_MAX
        )
    danger_x_max = 100.0 - WYSCOUT_DANGER_X_MIN
    return x <= danger_x_max and WYSCOUT_DANGER_Y_MIN <= y <= WYSCOUT_DANGER_Y_MAX
