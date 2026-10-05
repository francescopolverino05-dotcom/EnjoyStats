import { describe, expect, it } from "vitest";
import {
  BLOCK_SECONDS,
  buildCoverageReport,
  computeTeamStats,
  estimateXg,
  exportBothOnceSportXml,
  loadDefaultTags,
  onceSportNameForTagId,
  type MatchEvent,
  type MatchSetup,
  type PlayerRef,
} from "../src/index";

const homePlayer: PlayerRef = {
  id: "p1",
  name: "Rossi",
  number: 9,
  side: "home",
  confidence: "high",
};

const awayPlayer: PlayerRef = {
  id: "p2",
  name: "Bianchi",
  number: 10,
  side: "away",
  confidence: "high",
};

const setup: MatchSetup = {
  id: "match-1",
  homeTeam: "Pisa",
  awayTeam: "Perugia",
  homeKitColor: "#0000aa",
  awayKitColor: "#aa0000",
  homeAttacksLeftToRightFirstHalf: true,
  durationMinutes: 90,
  lineups: [homePlayer, awayPlayer],
};

function event(partial: Partial<MatchEvent> & Pick<MatchEvent, "id" | "tagId" | "side" | "player">): MatchEvent {
  const clockSeconds = partial.clockSeconds ?? 100;
  return {
    matchId: setup.id,
    onceSportName: onceSportNameForTagId(partial.tagId),
    clockSeconds,
    period: 1,
    successful: true,
    x: 85,
    y: 50,
    blockIndex: Math.floor(clockSeconds / BLOCK_SECONDS),
    ...partial,
  };
}

describe("default tags", () => {
  it("keeps exact Once Sport names", () => {
    const tags = loadDefaultTags();
    expect(tags.find((t) => t.id === "pass")?.onceSportName).toBe("Passaggi");
    expect(onceSportNameForTagId("corner")).toBe("Calcio d'angolo");
  });
});

describe("stats engine", () => {
  it("computes goals, xG estimate, pass accuracy, PPDA", () => {
    const events: MatchEvent[] = [
      event({ id: "1", tagId: "goal", side: "home", player: homePlayer, isGoal: true, shotOnTarget: true, insideBox: true }),
      event({ id: "2", tagId: "shot_on_target", side: "home", player: homePlayer, shotOnTarget: true, insideBox: true, clockSeconds: 120 }),
      event({ id: "3", tagId: "pass", side: "home", player: homePlayer, successful: true, clockSeconds: 130 }),
      event({ id: "4", tagId: "pass", side: "home", player: homePlayer, successful: false, clockSeconds: 140 }),
      event({ id: "5", tagId: "pass", side: "away", player: awayPlayer, clockSeconds: 150 }),
      event({ id: "6", tagId: "pass", side: "away", player: awayPlayer, clockSeconds: 160 }),
      event({ id: "7", tagId: "interception", side: "home", player: homePlayer, clockSeconds: 170 }),
    ];
    const home = computeTeamStats(setup, events, "home");
    expect(home.goals).toBe(1);
    expect(home.xgEstimate).toBeGreaterThan(0);
    expect(home.shots).toBe(2);
    expect(home.passAccuracy).toBe(50);
    expect(home.ppda).toBeGreaterThan(0);
    expect(estimateXg(events[0]!)).toBeGreaterThan(0.5);
  });
});

describe("coverage", () => {
  it("flags sparse 5-minute blocks for re-pass", () => {
    const events: MatchEvent[] = [
      event({ id: "1", tagId: "pass", side: "home", player: homePlayer, clockSeconds: 10 }),
    ];
    const report = buildCoverageReport(events, 10);
    expect(report).toHaveLength(2);
    expect(report[0]!.needsRepass).toBe(true);
    expect(report[0]!.homeEvents).toBe(1);
  });
});

describe("Once Sport XML export", () => {
  it("writes exact onceSportName and one file per team", () => {
    const events: MatchEvent[] = [
      event({ id: "a1", tagId: "pass", side: "home", player: homePlayer, clockSeconds: 65 }),
      event({ id: "a2", tagId: "goal", side: "away", player: awayPlayer, isGoal: true, clockSeconds: 200 }),
    ];
    const { home, away } = exportBothOnceSportXml(setup, events);
    expect(home).toContain("<analysis");
    expect(home).toContain("Passaggi");
    expect(home).toContain("(9) Rossi / Passaggi");
    expect(home).not.toContain("Bianchi");
    expect(away).toContain("Goal");
    expect(away).toContain("(10) Bianchi");
    expect(away).toContain('analysedTeam="Perugia"');
  });
});
