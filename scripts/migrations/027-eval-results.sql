-- Migration 027: Behavioral eval results + heartbeat stale detection
-- Phase 14: Quality & Observability

-- Eval results for trend tracking
CREATE TABLE IF NOT EXISTS eval_results (
    eval_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    suite         VARCHAR(100) NOT NULL,
    scenario      VARCHAR(200) NOT NULL,
    passed        BOOLEAN NOT NULL,
    score         NUMERIC(5, 3),
    cost_usd      NUMERIC(10, 6),
    details       JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eval_results_suite ON eval_results (suite, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_results_passed ON eval_results (passed, created_at DESC);

-- NOTE: session_health table already created in migration 025 (Phase 12).
-- Phase 14 heartbeat emitter writes to the existing session_health table.
-- Add partial index for stale detection (heartbeats older than 5 minutes):
CREATE INDEX IF NOT EXISTS idx_session_health_stale ON session_health (created_at)
    WHERE created_at < NOW() - INTERVAL '5 minutes';

-- Seed feature flags (all OFF by default)
INSERT INTO system_config (key, value) VALUES
    ('BEHAVIORAL_EVALS_ENABLED', 'false'::jsonb),
    ('HEARTBEAT_LIFECYCLE_ENABLED', 'false'::jsonb),
    ('LOG_REDACTION_ENABLED', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;
