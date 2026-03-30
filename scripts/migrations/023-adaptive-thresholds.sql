-- Migration 023: Adaptive thresholds + meta_evaluations
-- Phase 7: Self-modifying expansion thresholds via Thompson sampling bandits
-- Reference: Sutton & Barto, Reinforcement Learning (2018), Ch. 2.7

-- Bandit state storage (replaces 5 hardcoded thresholds in expansion.py)
CREATE TABLE IF NOT EXISTS adaptive_thresholds (
    id SERIAL PRIMARY KEY,
    threshold_name TEXT UNIQUE NOT NULL,
    alpha DECIMAL(10,4) NOT NULL,
    beta DECIMAL(10,4) NOT NULL,
    current_value DECIMAL(10,4),
    total_updates INTEGER DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Seed with warm-start priors: Beta(10, 2)
-- Mean = 10/(10+2) = 0.833 — strong prior belief that current thresholds are roughly correct
-- After ~50 observations, data dominates the prior (no random cold-start decisions)
INSERT INTO adaptive_thresholds (threshold_name, alpha, beta, current_value)
VALUES
    ('reply_rate_threshold', 10, 2, 1.5),
    ('interest_rate_threshold', 10, 2, 12.0),
    ('proposal_backlog_threshold', 10, 2, 3.0),
    ('uninvoiced_threshold', 10, 2, 2.0),
    ('missing_email_threshold', 10, 2, 10.0)
ON CONFLICT DO NOTHING;

-- Audit table: every threshold change is logged with rationale
CREATE TABLE IF NOT EXISTS meta_evaluations (
    id SERIAL PRIMARY KEY,
    threshold_name TEXT NOT NULL,
    old_value DECIMAL(10,4),
    new_value DECIMAL(10,4),
    change_reason TEXT,  -- 'bandit_update', 'experiment_winner', 'manual_override'
    confidence DECIMAL(4,3),
    sample_size INTEGER,
    conversion_rate_before DECIMAL(6,4),
    conversion_rate_after DECIMAL(6,4),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meta_evaluations_threshold
    ON meta_evaluations (threshold_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_adaptive_thresholds_name
    ON adaptive_thresholds (threshold_name);
