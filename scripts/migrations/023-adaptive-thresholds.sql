-- Migration 023: Adaptive Thresholds + Meta-Evaluations
-- Phase 7: Thompson sampling bandits for expansion engine
-- Feature flag: ENABLE_BANDIT_EXPANSION
--
-- Tables:
--   adaptive_thresholds  — bandit state (alpha/beta) per threshold
--   meta_evaluations     — audit log for every threshold change

BEGIN;

-- ---------------------------------------------------------------------------
-- adaptive_thresholds — stores Beta distribution parameters per threshold
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adaptive_thresholds (
    id              SERIAL PRIMARY KEY,
    threshold_name  TEXT UNIQUE NOT NULL,
    alpha           DECIMAL(10,4) NOT NULL DEFAULT 10.0,
    beta            DECIMAL(10,4) NOT NULL DEFAULT 2.0,
    current_value   DECIMAL(10,4),
    total_updates   INTEGER DEFAULT 0,
    last_updated    TIMESTAMPTZ DEFAULT NOW()
);

-- Seed with warm-start priors: Beta(10, 2) for all 5 expansion thresholds.
-- Mean = 10/12 = 0.833 — strong prior that current hardcoded values are reasonable.
-- After ~50 observations, data dominates the prior.
INSERT INTO adaptive_thresholds (threshold_name, alpha, beta, current_value)
VALUES
    ('reply_rate_threshold',         10, 2, 1.5),
    ('interest_rate_threshold',      10, 2, 12.0),
    ('proposal_backlog_threshold',   10, 2, 3.0),
    ('uninvoiced_threshold',         10, 2, 2.0),
    ('missing_email_threshold',      10, 2, 10.0)
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- meta_evaluations — audit log for every threshold change (ADAPT-06)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta_evaluations (
    id                      SERIAL PRIMARY KEY,
    threshold_name          TEXT NOT NULL,
    old_value               DECIMAL(10,4),
    new_value               DECIMAL(10,4),
    change_reason           TEXT,      -- 'bandit_update', 'experiment_winner', 'experiment_created:X', 'manual_override'
    confidence              DECIMAL(4,3),
    sample_size             INTEGER,
    conversion_rate_before  DECIMAL(6,4),
    conversion_rate_after   DECIMAL(6,4),
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meta_evaluations_threshold
    ON meta_evaluations (threshold_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_meta_evaluations_reason
    ON meta_evaluations (change_reason);


-- ---------------------------------------------------------------------------
-- Feature flag seed
-- ---------------------------------------------------------------------------
INSERT INTO system_config (key, value)
VALUES ('ENABLE_BANDIT_EXPANSION', 'false')
ON CONFLICT (key) DO NOTHING;

COMMIT;
