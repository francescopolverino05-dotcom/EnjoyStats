import {
  BLOCK_SECONDS,
  buildCoverageReport,
  computeTeamStats,
  exportBothOnceSportXml,
  loadDefaultTags,
  onceSportNameForTagId,
  type MatchEvent,
  type MatchSetup,
  type PlayerRef,
} from "@statman/core";

const homePlayers: PlayerRef[] = [
  { id: "h9", name: "Rossi", number: 9, position: "ST", side: "home", confidence: "high" },
  { id: "h8", name: "Verdi", number: 8, position: "CM", side: "home", confidence: "high" },
  { id: "h1", name: "Neri", number: 1, position: "GK", side: "home", confidence: "high" },
];

const awayPlayers: PlayerRef[] = [
  { id: "a10", name: "Bianchi", number: 10, position: "ST", side: "away", confidence: "high" },
  { id: "a6", name: "Gialli", number: 6, position: "CB", side: "away", confidence: "high" },
  { id: "a1", name: "Blu", number: 1, position: "GK", side: "away", confidence: "high" },
];

export const demoSetup: MatchSetup = {
  id: "demo-pisa-perugia",
  homeTeam: "Pisa",
  awayTeam: "Perugia",
  homeKitColor: "#1e3a8a",
  awayKitColor: "#dc2626",
  homeAttacksLeftToRightFirstHalf: true,
  durationMinutes: 90,
  videoUrl: "",
  lineups: [...homePlayers, ...awayPlayers],
};

function evt(
  id: string,
  tagId: string,
  side: "home" | "away",
  player: PlayerRef,
  clockSeconds: number,
  extra: Partial<MatchEvent> = {},
): MatchEvent {
  return {
    id,
    matchId: demoSetup.id,
    tagId,
    onceSportName: onceSportNameForTagId(tagId),
    side,
    player,
    clockSeconds,
    period: clockSeconds >= 45 * 60 ? 2 : 1,
    successful: extra.successful ?? true,
    x: extra.x ?? 70,
    y: extra.y ?? 50,
    blockIndex: Math.floor(clockSeconds / BLOCK_SECONDS),
    ...extra,
  };
}

/** Dense enough demo timeline for UI / export smoke. */
export const demoEvents: MatchEvent[] = [
  evt("e1", "pass", "home", homePlayers[1]!, 95),
  evt("e2", "progressive_pass", "home", homePlayers[1]!, 110, { successful: true, endX: 88, endY: 48 }),
  evt("e3", "shot_on_target", "home", homePlayers[0]!, 125, { shotOnTarget: true, insideBox: true, x: 88 }),
  evt("e4", "goal", "home", homePlayers[0]!, 128, { isGoal: true, shotOnTarget: true, insideBox: true, x: 90 }),
  evt("e5", "pass", "away", awayPlayers[0]!, 400),
  evt("e6", "cross", "away", awayPlayers[0]!, 420, { successful: false, x: 75, y: 18 }),
  evt("e7", "shot_off_target", "away", awayPlayers[0]!, 430, { shotOnTarget: false, insideBox: false, x: 78 }),
  evt("e8", "save", "home", homePlayers[2]!, 432),
  evt("e9", "interception", "home", homePlayers[1]!, 900),
  evt("e10", "recovery", "away", awayPlayers[1]!, 920, { third: "final" }),
  evt("e11", "ground_duel", "home", homePlayers[1]!, 1100, { successful: true }),
  evt("e12", "aerial_duel", "away", awayPlayers[1]!, 1120, { successful: true }),
  evt("e13", "corner", "home", homePlayers[1]!, 2000),
  evt("e14", "goal", "away", awayPlayers[0]!, 2500, { isGoal: true, shotOnTarget: true, insideBox: true, x: 12 }),
  evt("e15", "foul", "home", homePlayers[1]!, 2600),
  evt("e16", "yellow_card", "home", homePlayers[1]!, 2605),
  evt("e17", "pass", "away", awayPlayers[0]!, 3000, { successful: false }),
  evt("e18", "through_ball", "away", awayPlayers[0]!, 3050, { successful: true, endX: 15 }),
  evt("e19", "long_ball", "home", homePlayers[2]!, 3100, { successful: false }),
  evt("e20", "throw_in", "away", awayPlayers[1]!, 3200),
];

export function demoCoverage() {
  return buildCoverageReport(demoEvents, demoSetup.durationMinutes);
}

export function demoTeamStats() {
  return {
    home: computeTeamStats(demoSetup, demoEvents, "home"),
    away: computeTeamStats(demoSetup, demoEvents, "away"),
  };
}

export function demoXml() {
  return exportBothOnceSportXml(demoSetup, demoEvents);
}

export function demoTags() {
  return loadDefaultTags();
}
