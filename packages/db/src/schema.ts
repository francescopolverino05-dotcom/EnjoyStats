import { integer, real, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const matches = sqliteTable("matches", {
  id: text("id").primaryKey(),
  homeTeam: text("home_team").notNull(),
  awayTeam: text("away_team").notNull(),
  homeKitColor: text("home_kit_color").notNull().default("#1d4ed8"),
  awayKitColor: text("away_kit_color").notNull().default("#dc2626"),
  homeAttacksLeftToRightFirstHalf: integer("home_l2r_1h", { mode: "boolean" })
    .notNull()
    .default(true),
  durationMinutes: real("duration_minutes").notNull().default(90),
  videoPath: text("video_path"),
  videoUrl: text("video_url"),
  status: text("status").notNull().default("draft"),
  createdAt: text("created_at").notNull(),
});

export const players = sqliteTable("players", {
  id: text("id").primaryKey(),
  matchId: text("match_id").notNull(),
  side: text("side").notNull(),
  name: text("name").notNull(),
  number: integer("number"),
  position: text("position"),
  confidence: text("confidence").notNull().default("high"),
});

export const events = sqliteTable("events", {
  id: text("id").primaryKey(),
  matchId: text("match_id").notNull(),
  tagId: text("tag_id").notNull(),
  onceSportName: text("once_sport_name").notNull(),
  side: text("side").notNull(),
  playerId: text("player_id").notNull(),
  clockSeconds: integer("clock_seconds").notNull(),
  period: integer("period").notNull(),
  successful: integer("successful", { mode: "boolean" }).notNull().default(true),
  isGoal: integer("is_goal", { mode: "boolean" }).notNull().default(false),
  shotOnTarget: integer("shot_on_target", { mode: "boolean" }),
  shotBlocked: integer("shot_blocked", { mode: "boolean" }),
  insideBox: integer("inside_box", { mode: "boolean" }),
  headed: integer("headed", { mode: "boolean" }),
  x: real("x").notNull(),
  y: real("y").notNull(),
  endX: real("end_x"),
  endY: real("end_y"),
  third: text("third"),
  notes: text("notes"),
  blockIndex: integer("block_index").notNull(),
});

export const coverageBlocks = sqliteTable("coverage_blocks", {
  id: text("id").primaryKey(),
  matchId: text("match_id").notNull(),
  blockIndex: integer("block_index").notNull(),
  startSeconds: integer("start_seconds").notNull(),
  endSeconds: integer("end_seconds").notNull(),
  homeEvents: integer("home_events").notNull().default(0),
  awayEvents: integer("away_events").notNull().default(0),
  processed: integer("processed", { mode: "boolean" }).notNull().default(false),
  needsRepass: integer("needs_repass", { mode: "boolean" }).notNull().default(true),
});
