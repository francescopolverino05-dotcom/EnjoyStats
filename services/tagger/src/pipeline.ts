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
 * Walk every 5-minute block in order. Empty/sparse blocks are flagged for re-pass.
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
  const coverage = buildCoverageReport(events, input.durationMinutes).map((row) => ({
    ...row,
    processed: true,
    needsRepass: row.homeEvents + row.awayEvents < BLOCK_MIN_EVENTS,
  }));
  return {
    events,
    coverage,
    repassBlocks: coverage.filter((b) => b.needsRepass).map((b) => b.blockIndex),
  };
}
