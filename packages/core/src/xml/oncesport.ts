import type { MatchEvent, MatchSetup, TeamSide } from "../types";

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function formatClock(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function playerLabel(event: MatchEvent): string {
  const number = event.player.number != null ? `(${event.player.number}) ` : "";
  return `${number}${event.player.name}`.trim();
}

/**
 * Export one team as Once Sport Analyser ``<analysis>`` XML.
 * Tag names are written exactly as ``onceSportName`` — never renamed.
 */
export function exportOnceSportXml(
  setup: MatchSetup,
  events: MatchEvent[],
  side: TeamSide,
): string {
  const teamName = side === "home" ? setup.homeTeam : setup.awayTeam;
  const oppName = side === "home" ? setup.awayTeam : setup.homeTeam;
  const sideEvents = events
    .filter((e) => e.side === side)
    .slice()
    .sort((a, b) => a.clockSeconds - b.clockSeconds);

  const actions = sideEvents
    .map((event) => {
      const actionName = `${playerLabel(event)} / ${event.onceSportName}`;
      return [
        `    <action`,
        `      id="${escapeXml(event.id)}"`,
        `      actionName="${escapeXml(actionName)}"`,
        `      actionType="Other"`,
        `      startTime="${formatClock(event.clockSeconds)}"`,
        `      endTime="${formatClock(event.clockSeconds + 2)}"`,
        `      fieldMapper="[{&quot;x&quot;:${event.x},&quot;y&quot;:${event.y}}]"`,
        `    />`,
      ].join("\n");
    })
    .join("\n");

  const title = `${setup.homeTeam} v ${setup.awayTeam}`;
  return [
    `<?xml version="1.0" encoding="utf-8"?>`,
    `<analysis`,
    `  id="${escapeXml(setup.id)}-${side}"`,
    `  title="${escapeXml(title)}"`,
    `  type="0"`,
    `  videoFilepath="${escapeXml(setup.videoPath || setup.videoUrl || "")}"`,
    `  analysedTeam="${escapeXml(teamName)}"`,
    `  oppositionTeam="${escapeXml(oppName)}"`,
    `>`,
    `  <info />`,
    `  <actions>`,
    actions,
    `  </actions>`,
    `</analysis>`,
    ``,
  ].join("\n");
}

export function exportBothOnceSportXml(
  setup: MatchSetup,
  events: MatchEvent[],
): { home: string; away: string } {
  return {
    home: exportOnceSportXml(setup, events, "home"),
    away: exportOnceSportXml(setup, events, "away"),
  };
}
