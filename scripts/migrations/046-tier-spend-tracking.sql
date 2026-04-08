-- Phase 41: Tier-based spend tracking
-- Created: 2026-04-07
--
-- Tracks every LLM call with tier, daemon, model, tokens, cost, latency.
-- Powers the per-daemon virtual budget enforcement and the daily/monthly
-- spend dashboards in the War Room.
--
-- Phase 42 adds Langfuse traces alongside this; Langfuse stores per-prompt
-- detail, this table stores aggregate metrics for fast budget queries.

BEGIN;

-- ============================================================================
-- Main spend log
-- ============================================================================

CREATE TABLE IF NOT EXISTS tier_spend_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tier TEXT NOT NULL,
    daemon TEXT NOT NULL,
    pipeline_stage TEXT,
    task_class TEXT,
    chain_depth INTEGER NOT NULL DEFAULT 0,
    architect_call BOOLEAN NOT NULL DEFAULT FALSE,
    model_used TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'unknown',
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 8) NOT NULL,
    latency_ms INTEGER,
    success BOOLEAN NOT NULL,
    error_message TEXT,
    request_id TEXT,
    fallback_depth INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    verifier_layer_failed TEXT,           -- L1, L2, L3, L4 if verifier failed
    escalated_from_tier TEXT,             -- The tier we tried first if this is a retry
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_tier_spend_timestamp ON tier_spend_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_tier_spend_daemon ON tier_spend_log(daemon, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_tier_spend_tier ON tier_spend_log(tier, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_tier_spend_request_id ON tier_spend_log(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tier_spend_failures ON tier_spend_log(success, timestamp DESC) WHERE success = false;

-- ============================================================================
-- Aggregation views — used by spend CLI and War Room dashboard
-- ============================================================================

CREATE OR REPLACE VIEW daily_spend_by_tier AS
SELECT
    DATE(timestamp) AS date,
    tier,
    SUM(cost_usd) AS total_cost,
    COUNT(*) AS request_count,
    AVG(latency_ms) AS avg_latency_ms,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
    SUM(input_tokens) AS total_input_tokens,
    SUM(output_tokens) AS total_output_tokens,
    SUM(cached_tokens) AS total_cached_tokens,
    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::FLOAT / GREATEST(COUNT(*), 1) AS cache_hit_rate,
    SUM(CASE WHEN success THEN 0 ELSE 1 END) AS failures
FROM tier_spend_log
GROUP BY DATE(timestamp), tier;

CREATE OR REPLACE VIEW daily_spend_by_daemon AS
SELECT
    DATE(timestamp) AS date,
    daemon,
    SUM(cost_usd) AS total_cost,
    COUNT(*) AS request_count,
    AVG(latency_ms) AS avg_latency_ms,
    SUM(CASE WHEN success THEN 0 ELSE 1 END) AS failures,
    SUM(CASE WHEN escalated_from_tier IS NOT NULL THEN 1 ELSE 0 END) AS escalations
FROM tier_spend_log
GROUP BY DATE(timestamp), daemon;

CREATE OR REPLACE VIEW monthly_spend_by_daemon AS
SELECT
    DATE_TRUNC('month', timestamp) AS month,
    daemon,
    SUM(cost_usd) AS total_cost,
    COUNT(*) AS request_count,
    SUM(CASE WHEN escalated_from_tier IS NOT NULL THEN 1 ELSE 0 END) AS escalations,
    SUM(CASE WHEN tier IN ('local', 'local-heavy') THEN 1 ELSE 0 END)::FLOAT
        / GREATEST(COUNT(*), 1) AS local_share
FROM tier_spend_log
GROUP BY DATE_TRUNC('month', timestamp), daemon;

CREATE OR REPLACE VIEW escalation_patterns AS
SELECT
    daemon,
    escalated_from_tier AS from_tier,
    tier AS to_tier,
    verifier_layer_failed,
    COUNT(*) AS occurrences,
    AVG(cost_usd) AS avg_cost,
    AVG(latency_ms) AS avg_latency_ms
FROM tier_spend_log
WHERE escalated_from_tier IS NOT NULL
GROUP BY daemon, escalated_from_tier, tier, verifier_layer_failed
ORDER BY occurrences DESC;

CREATE OR REPLACE VIEW daily_local_share AS
SELECT
    DATE(timestamp) AS date,
    daemon,
    SUM(CASE WHEN tier IN ('local', 'local-heavy') THEN 1 ELSE 0 END) AS local_calls,
    COUNT(*) AS total_calls,
    SUM(CASE WHEN tier IN ('local', 'local-heavy') THEN 1 ELSE 0 END)::FLOAT
        / GREATEST(COUNT(*), 1) AS local_share
FROM tier_spend_log
GROUP BY DATE(timestamp), daemon;

-- ============================================================================
-- Per-daemon virtual budgets (matches litellm_config.yaml team_budgets)
-- ============================================================================

CREATE TABLE IF NOT EXISTS daemon_budget_caps (
    daemon TEXT PRIMARY KEY,
    monthly_cap_usd NUMERIC(10, 2) NOT NULL,
    daily_cap_usd NUMERIC(10, 2),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO daemon_budget_caps (daemon, monthly_cap_usd, daily_cap_usd) VALUES
    ('titan',      90.0, 5.0),
    ('clawdbot',   80.0, 4.0),
    ('deerflow',   40.0, 2.0),
    ('ruflo',      60.0, 3.0),
    ('hermes',     30.0, 1.5),
    ('perseus',    20.0, 1.0),
    ('openjarvis', 30.0, 1.5),
    ('conway',     15.0, 0.75)
ON CONFLICT (daemon) DO NOTHING;

-- ============================================================================
-- Daily aggregation (refreshed by Perseus scheduler nightly)
-- ============================================================================

CREATE TABLE IF NOT EXISTS daily_spend_summary (
    summary_date DATE PRIMARY KEY,
    total_cost_usd NUMERIC(10, 2) NOT NULL,
    total_requests INTEGER NOT NULL,
    local_share_pct NUMERIC(5, 2) NOT NULL,
    escalation_rate_pct NUMERIC(5, 2) NOT NULL,
    avg_p95_latency_ms INTEGER,
    failures INTEGER NOT NULL DEFAULT 0,
    by_tier JSONB NOT NULL,
    by_daemon JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
