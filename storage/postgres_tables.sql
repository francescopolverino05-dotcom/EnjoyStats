-- EnjoyStats PostgreSQL DDL
-- Hybrid layout for live football tagging:
--   * OffensiveStats is flattened into typed columns for leaderboard scans.
--   * DefensiveStats and DistributionStats are stored as JSONB documents
--     so nested success/total ratios stay intact without wide tables.

CREATE TABLE IF NOT EXISTS matches (
    match_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    home_team_id    UUID,
    away_team_id    UUID,
    competition     TEXT,
    season          TEXT,
    matchday        SMALLINT CHECK (matchday IS NULL OR matchday >= 1),
    venue           TEXT,
    kickoff_at      TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled', 'live', 'finished', 'cancelled')),
    home_score      INTEGER NOT NULL DEFAULT 0 CHECK (home_score >= 0),
    away_score      INTEGER NOT NULL DEFAULT 0 CHECK (away_score >= 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS matches_status_kickoff_idx
    ON matches (status, kickoff_at DESC);

CREATE INDEX IF NOT EXISTS matches_home_team_id_idx
    ON matches (home_team_id);

CREATE INDEX IF NOT EXISTS matches_away_team_id_idx
    ON matches (away_team_id);

CREATE TABLE IF NOT EXISTS player_match_stats (
    player_id                       UUID NOT NULL,
    match_id                        UUID NOT NULL
                                    REFERENCES matches (match_id) ON DELETE CASCADE,
    stats_id                        UUID NOT NULL DEFAULT gen_random_uuid(),
    team_id                         UUID NOT NULL,
    jersey_number                   SMALLINT
                                    CHECK (jersey_number IS NULL OR jersey_number BETWEEN 1 AND 99),
    player_name                     TEXT NOT NULL DEFAULT '',
    position                        TEXT NOT NULL DEFAULT '',

    -- Flattened OffensiveStats (leaderboard / ranking columns)
    minutes                         NUMERIC(5, 1) NOT NULL DEFAULT 0
                                    CHECK (minutes >= 0 AND minutes <= 150),
    goals                           INTEGER NOT NULL DEFAULT 0 CHECK (goals >= 0),
    assists                         INTEGER NOT NULL DEFAULT 0 CHECK (assists >= 0),
    total_shots                     INTEGER NOT NULL DEFAULT 0 CHECK (total_shots >= 0),
    shots_on_target                 INTEGER NOT NULL DEFAULT 0 CHECK (shots_on_target >= 0),
    blocked_shots                   INTEGER NOT NULL DEFAULT 0 CHECK (blocked_shots >= 0),
    missed_shots                    INTEGER NOT NULL DEFAULT 0 CHECK (missed_shots >= 0),
    shots_inside_penalty_area       INTEGER NOT NULL DEFAULT 0 CHECK (shots_inside_penalty_area >= 0),
    shots_outside_penalty_area      INTEGER NOT NULL DEFAULT 0 CHECK (shots_outside_penalty_area >= 0),
    offsides                        INTEGER NOT NULL DEFAULT 0 CHECK (offsides >= 0),
    freekicks                       INTEGER NOT NULL DEFAULT 0 CHECK (freekicks >= 0),
    corners                         INTEGER NOT NULL DEFAULT 0 CHECK (corners >= 0),
    penalty_kicks                   INTEGER NOT NULL DEFAULT 0 CHECK (penalty_kicks >= 0),
    throw_ins                       INTEGER NOT NULL DEFAULT 0 CHECK (throw_ins >= 0),

    -- Nested DefensiveStats / DistributionStats / PossessionStats as JSONB
    defensive                       JSONB NOT NULL DEFAULT '{}'::jsonb
                                    CHECK (jsonb_typeof(defensive) = 'object'),
    distribution                    JSONB NOT NULL DEFAULT '{}'::jsonb
                                    CHECK (jsonb_typeof(distribution) = 'object'),
    possession                      JSONB NOT NULL DEFAULT '{}'::jsonb
                                    CHECK (jsonb_typeof(possession) = 'object'),

    collected_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (player_id, match_id),
    CONSTRAINT player_match_stats_stats_id_key UNIQUE (stats_id),
    CONSTRAINT player_match_stats_shot_outcomes_chk CHECK (
        total_shots = shots_on_target + blocked_shots + missed_shots
    ),
    CONSTRAINT player_match_stats_shot_zones_chk CHECK (
        total_shots = shots_inside_penalty_area + shots_outside_penalty_area
    ),
    CONSTRAINT player_match_stats_goals_subset_chk CHECK (
        goals <= shots_on_target
    ),
    CONSTRAINT player_match_stats_penalties_subset_chk CHECK (
        penalty_kicks <= total_shots
    )
);

-- Standard GIN indexes for JSONB containment / key existence queries.
CREATE INDEX IF NOT EXISTS player_match_stats_defensive_gin_idx
    ON player_match_stats USING GIN (defensive);

CREATE INDEX IF NOT EXISTS player_match_stats_distribution_gin_idx
    ON player_match_stats USING GIN (distribution);

CREATE INDEX IF NOT EXISTS player_match_stats_possession_gin_idx
    ON player_match_stats USING GIN (possession);

-- B-tree helpers for flattened offensive leaderboards.
CREATE INDEX IF NOT EXISTS player_match_stats_match_goals_idx
    ON player_match_stats (match_id, goals DESC);

CREATE INDEX IF NOT EXISTS player_match_stats_match_assists_idx
    ON player_match_stats (match_id, assists DESC);

CREATE INDEX IF NOT EXISTS player_match_stats_match_shots_idx
    ON player_match_stats (match_id, total_shots DESC, shots_on_target DESC);

CREATE INDEX IF NOT EXISTS player_match_stats_team_id_idx
    ON player_match_stats (team_id, match_id);

-- Idempotent column adds for volumes created before the expanded spec.
ALTER TABLE player_match_stats ADD COLUMN IF NOT EXISTS player_name TEXT NOT NULL DEFAULT '';
ALTER TABLE player_match_stats ADD COLUMN IF NOT EXISTS penalty_kicks INTEGER NOT NULL DEFAULT 0;
ALTER TABLE player_match_stats ADD COLUMN IF NOT EXISTS throw_ins INTEGER NOT NULL DEFAULT 0;
ALTER TABLE player_match_stats ADD COLUMN IF NOT EXISTS possession JSONB NOT NULL DEFAULT '{}'::jsonb;
