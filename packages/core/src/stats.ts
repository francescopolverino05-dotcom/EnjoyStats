import type { BlockCoverage, MatchEvent, MatchSetup, PitchThird, TeamMatchStats, TeamSide } from "./types";

export const BLOCK_SECONDS = 5 * 60;
/** Re-pass if a processed block has fewer than this many events total. */
export const BLOCK_MIN_EVENTS = 8;

export function blockIndexForClock(clockSeconds: number): number {
  return Math.max(0, Math.floor(clockSeconds / BLOCK_SECONDS));
}

export function expectedBlockCount(durationMinutes: number): number {
  const seconds = Math.max(1, Math.round(durationMinutes * 60));
  return Math.ceil(seconds / BLOCK_SECONDS);
}

export function buildCoverageReport(
  events: MatchEvent[],
  durationMinutes: number,
): BlockCoverage[] {
  const count = expectedBlockCount(durationMinutes);
  const blocks: BlockCoverage[] = [];
  for (let i = 0; i < count; i += 1) {
    const startSeconds = i * BLOCK_SECONDS;
    const endSeconds = startSeconds + BLOCK_SECONDS;
    const inBlock = events.filter((e) => e.blockIndex === i);
    const homeEvents = inBlock.filter((e) => e.side === "home").length;
    const awayEvents = inBlock.filter((e) => e.side === "away").length;
    const total = homeEvents + awayEvents;
    const processed = total > 0 || i < count; // worker marks processed; empty = needs re-pass
    blocks.push({
      blockIndex: i,
      startSeconds,
      endSeconds,
      homeEvents,
      awayEvents,
      processed,
      needsRepass: total < BLOCK_MIN_EVENTS,
    });
  }
  return blocks;
}

export function classifyThird(x: number, side: TeamSide, homeL2R: boolean): PitchThird {
  // Normalize so higher x is always toward the side's attacking goal.
  const attackingRight =
    side === "home" ? homeL2R : !homeL2R;
  const attackX = attackingRight ? x : 100 - x;
  if (attackX < 33.333) return "defensive";
  if (attackX < 66.666) return "middle";
  return "final";
}

function isShot(tagId: string): boolean {
  return tagId.startsWith("shot") || tagId === "goal";
}

function isPassFamily(tagId: string): boolean {
  return (
    tagId === "pass" ||
    tagId === "key_pass" ||
    tagId === "progressive_pass" ||
    tagId === "through_ball" ||
    tagId === "long_ball" ||
    tagId === "cross" ||
    tagId === "assist"
  );
}

/** Simple location-based xG estimate (labelled estimate in UI). */
export function estimateXg(event: MatchEvent): number {
  if (!isShot(event.tagId) && !event.isGoal) return 0;
  if (event.isGoal || event.tagId === "goal") return 0.7;
  const dist = Math.hypot(100 - event.x, event.y - 50);
  let base = Math.max(0.02, Math.min(0.45, 0.55 - dist / 120));
  if (event.insideBox) base += 0.08;
  if (event.headed) base *= 0.75;
  if (event.shotBlocked) base *= 0.35;
  if (event.shotOnTarget === false) base *= 0.5;
  return Math.round(base * 100) / 100;
}

export function computeTeamStats(
  setup: MatchSetup,
  events: MatchEvent[],
  side: TeamSide,
): TeamMatchStats {
  const teamEvents = events.filter((e) => e.side === side);
  const oppEvents = events.filter((e) => e.side !== side);
  const teamName = side === "home" ? setup.homeTeam : setup.awayTeam;

  const shots = teamEvents.filter((e) => isShot(e.tagId));
  const goals = teamEvents.filter((e) => e.isGoal || e.tagId === "goal").length;
  const shotsOnTarget = shots.filter((e) => e.shotOnTarget || e.isGoal || e.tagId === "goal").length;
  const shotsBlocked = shots.filter((e) => e.shotBlocked).length;
  const shotsMissed = Math.max(0, shots.length - shotsOnTarget - shotsBlocked);
  const shotsInsideBox = shots.filter((e) => e.insideBox).length;
  const headedShots = shots.filter((e) => e.headed).length;
  const xgEstimate = Math.round(shots.reduce((s, e) => s + estimateXg(e), 0) * 100) / 100;

  const passes = teamEvents.filter((e) => isPassFamily(e.tagId));
  const passesCompleted = passes.filter((e) => e.successful).length;
  const keyPasses = teamEvents.filter((e) => e.tagId === "key_pass").length;
  const progressive = teamEvents.filter((e) => e.tagId === "progressive_pass");
  const throughBalls = teamEvents.filter((e) => e.tagId === "through_ball");
  const longBalls = teamEvents.filter((e) => e.tagId === "long_ball");
  const crosses = teamEvents.filter((e) => e.tagId === "cross");

  const intoFinal = passes.filter((e) => e.third === "final" || (e.endX != null && classifyThird(e.endX, side, setup.homeAttacksLeftToRightFirstHalf) === "final"));
  const intoBox = passes.filter((e) => e.endX != null && e.endX >= 83);

  const ground = teamEvents.filter((e) => e.tagId === "ground_duel");
  const aerial = teamEvents.filter((e) => e.tagId === "aerial_duel");
  const recoveries = teamEvents.filter((e) => e.tagId === "recovery");
  const recoveriesHigh = recoveries.filter((e) => e.third === "final").length;

  const oppPasses = oppEvents.filter((e) => isPassFamily(e.tagId)).length;
  const defensiveActions =
    teamEvents.filter((e) =>
      ["foul", "interception", "ground_duel", "aerial_duel"].includes(e.tagId),
    ).length || 1;
  const ppda = Math.round((oppPasses / defensiveActions) * 10) / 10;

  const possessionEstimate =
    events.length === 0
      ? 50
      : Math.round((teamEvents.length / events.length) * 1000) / 10;

  return {
    side,
    teamName,
    goals,
    xgEstimate,
    shots: shots.length,
    shotsOnTarget,
    shotAccuracy: shots.length ? Math.round((shotsOnTarget / shots.length) * 100) : 0,
    shotsBlocked,
    shotsMissed,
    shotsInsideBox,
    shotsOutsideBox: Math.max(0, shots.length - shotsInsideBox),
    headedShots,
    possessionEstimate,
    passes: passes.length,
    passesCompleted,
    passAccuracy: passes.length ? Math.round((passesCompleted / passes.length) * 100) : 0,
    keyPasses,
    progressivePassesAttempted: progressive.length,
    progressivePassesCompleted: progressive.filter((e) => e.successful).length,
    intoFinalThirdAttempted: intoFinal.length,
    intoFinalThirdCompleted: intoFinal.filter((e) => e.successful).length,
    intoBoxAttempted: intoBox.length,
    intoBoxCompleted: intoBox.filter((e) => e.successful).length,
    crosses: crosses.length,
    crossAccuracy: crosses.length
      ? Math.round((crosses.filter((e) => e.successful).length / crosses.length) * 100)
      : 0,
    longBalls: longBalls.length,
    longBallAccuracy: longBalls.length
      ? Math.round((longBalls.filter((e) => e.successful).length / longBalls.length) * 100)
      : 0,
    throughBalls: throughBalls.length,
    throughBallAccuracy: throughBalls.length
      ? Math.round((throughBalls.filter((e) => e.successful).length / throughBalls.length) * 100)
      : 0,
    groundDuelsWon: ground.filter((e) => e.successful).length,
    groundDuelsTotal: ground.length,
    aerialDuelsWon: aerial.filter((e) => e.successful).length,
    aerialDuelsTotal: aerial.length,
    interceptions: teamEvents.filter((e) => e.tagId === "interception").length,
    recoveries: recoveries.length,
    recoveriesHigh,
    ballsLost: teamEvents.filter((e) => e.tagId === "ball_lost").length,
    blocks: teamEvents.filter((e) => e.tagId === "shot_blocked" || e.shotBlocked).length,
    saves: teamEvents.filter((e) => e.tagId === "save").length,
    foulsCommitted: teamEvents.filter((e) => e.tagId === "foul").length,
    foulsWon: teamEvents.filter((e) => e.tagId === "foul_won").length,
    yellowCards: teamEvents.filter((e) => e.tagId === "yellow_card").length,
    redCards: teamEvents.filter((e) => e.tagId === "red_card").length,
    offsides: teamEvents.filter((e) => e.tagId === "offside").length,
    corners: teamEvents.filter((e) => e.tagId === "corner").length,
    freeKicks: teamEvents.filter((e) => e.tagId === "free_kick").length,
    throwIns: teamEvents.filter((e) => e.tagId === "throw_in").length,
    ppda,
  };
}
