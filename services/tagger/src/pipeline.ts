import {
  BLOCK_MIN_EVENTS,
  BLOCK_SECONDS,
  buildCoverageReport,
  expectedBlockCount,
  type BlockCoverage,
  type MatchEvent,
} from "@statman/core";

export interface TagBlockInput {
  matchId: string;
  durationMinutes: number;
  /** Worker callback: tag one 5-minute window. Must not skip. */
  tagBlock: (
    blockIndex: number,
    startSeconds: number,
    endSeconds: number,
  ) => Promise<MatchEvent[]>;
}

/**
 * Walk every 5-minute block in order. Empty/sparse blocks are flagged and
 * re-tagged once (Step C re-pass) before the final coverage report.
 */
export async function runBlockPipeline(input: TagBlockInput): Promise<{
  events: MatchEvent[];
  coverage: BlockCoverage[];
  repassBlocks: number[];
}> {
  const count = expectedBlockCount(input.durationMinutes);
  const events: MatchEvent[] = [];
  for (let blockIndex = 0; blockIndex < count; blockIndex += 1) {
    const startSeconds = blockIndex * BLOCK_SECONDS;
    const endSeconds = startSeconds + BLOCK_SECONDS;
    const batch = await input.tagBlock(blockIndex, startSeconds, endSeconds);
    for (const event of batch) {
      events.push({ ...event, blockIndex, matchId: input.matchId });
    }
  }

  let coverage = buildCoverageReport(events, input.durationMinutes).map((row) => ({
    ...row,
    processed: true,
    needsRepass: row.homeEvents + row.awayEvents < BLOCK_MIN_EVENTS,
  }));
  let repassBlocks = coverage.filter((b) => b.needsRepass).map((b) => b.blockIndex);

  // Step C: watch sparse windows again and replace that block's events.
  if (repassBlocks.length > 0) {
    const kept = events.filter((event) => !repassBlocks.includes(event.blockIndex));
    const refreshed: MatchEvent[] = [...kept];
    for (const blockIndex of repassBlocks) {
      const startSeconds = blockIndex * BLOCK_SECONDS;
      const endSeconds = startSeconds + BLOCK_SECONDS;
      const batch = await input.tagBlock(blockIndex, startSeconds, endSeconds);
      for (const event of batch) {
        refreshed.push({ ...event, blockIndex, matchId: input.matchId });
      }
    }
    events.length = 0;
    events.push(...refreshed);
    coverage = buildCoverageReport(events, input.durationMinutes).map((row) => ({
      ...row,
      processed: true,
      needsRepass: row.homeEvents + row.awayEvents < BLOCK_MIN_EVENTS,
    }));
    repassBlocks = coverage.filter((b) => b.needsRepass).map((b) => b.blockIndex);
  }

  return {
    events,
    coverage,
    repassBlocks,
  };
}
