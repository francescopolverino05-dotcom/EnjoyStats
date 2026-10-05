/** Shared STAT MAN domain types. */

export type TeamSide = "home" | "away";
export type PitchThird = "defensive" | "middle" | "final";
export type Confidence = "high" | "low";
export type TagPanel = "home_attacking" | "away_defending" | "shared";
export type TagColor = "green" | "orange" | "neutral";
export type StatKind =
  | "pass"
  | "progressive_pass"
  | "long_ball"
  | "cross"
  | "shot"
  | "save"
  | "corner"
  | "free_kick"
  | "throw_in"
  | "offside"
  | "foul"
  | "foul_won"
  | "aerial_duel"
  | "ground_duel"
  | "interception"
  | "recovery_high"
  | "ball_lost"
  | "period_marker"
  | "substitution"
  | "goal"
  | "assist"
  | "key_pass"
  | "through_ball"
  | "recovery";

export interface TagDefinition {
  id: string;
  /** Display name in STAT MAN UI */
  label: string;
  /** Exact Once Sport Analyser button label — never renamed on export */
  onceSportName: string;
  panel: TagPanel;
  color: TagColor;
  /** How the stats engine classifies this button */
  statKind: StatKind;
}

export interface PlayerRef {
  id: string;
  name: string;
  number?: number;
  position?: string;
  side: TeamSide;
  confidence: Confidence;
}

export interface MatchSetup {
  id: string;
  homeTeam: string;
  awayTeam: string;
  homeKitColor: string;
  awayKitColor: string;
  /** Attack direction in 1H for home: left_to_right means home attacks +x */
  homeAttacksLeftToRightFirstHalf: boolean;
  durationMinutes: number;
  videoPath?: string;
  videoUrl?: string;
  lineups: PlayerRef[];
}

export interface MatchEvent {
  id: string;
  matchId: string;
  tagId: string;
  /** Exact Once Sport name at tag time */
  onceSportName: string;
  side: TeamSide;
  player: PlayerRef;
  /** Seconds from kickoff (match clock) */
  clockSeconds: number;
  period: 1 | 2;
  successful: boolean;
  isGoal?: boolean;
  shotOnTarget?: boolean;
  shotBlocked?: boolean;
  insideBox?: boolean;
  headed?: boolean;
  x: number;
  y: number;
  endX?: number;
  endY?: number;
  third?: PitchThird;
  notes?: string;
  blockIndex: number;
}

export interface BlockCoverage {
  blockIndex: number;
  startSeconds: number;
  endSeconds: number;
  homeEvents: number;
  awayEvents: number;
  processed: boolean;
  needsRepass: boolean;
}

export interface TeamMatchStats {
  side: TeamSide;
  teamName: string;
  goals: number;
  xgEstimate: number;
  shots: number;
  shotsOnTarget: number;
  shotAccuracy: number;
  shotsBlocked: number;
  shotsMissed: number;
  shotsInsideBox: number;
  shotsOutsideBox: number;
  headedShots: number;
  possessionEstimate: number;
  passes: number;
  passesCompleted: number;
  passAccuracy: number;
  keyPasses: number;
  progressivePassesAttempted: number;
  progressivePassesCompleted: number;
  intoFinalThirdAttempted: number;
  intoFinalThirdCompleted: number;
  intoBoxAttempted: number;
  intoBoxCompleted: number;
  crosses: number;
  crossAccuracy: number;
  longBalls: number;
  longBallAccuracy: number;
  throughBalls: number;
  throughBallAccuracy: number;
  groundDuelsWon: number;
  groundDuelsTotal: number;
  aerialDuelsWon: number;
  aerialDuelsTotal: number;
  interceptions: number;
  recoveries: number;
  recoveriesHigh: number;
  ballsLost: number;
  blocks: number;
  saves: number;
  foulsCommitted: number;
  foulsWon: number;
  yellowCards: number;
  redCards: number;
  offsides: number;
  corners: number;
  freeKicks: number;
  throwIns: number;
  ppda: number;
}
