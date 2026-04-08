-- Phase 44: Shadow diff tracking
-- Created: 2026-04-07
--
-- Shadow mode runs both old (direct-Anthropic) and new (LiteLLM proxy) backends
-- in parallel for the same prompt, returns the OLD result, and logs the diff.
-- After 48-72h of shadow data, operator can confidently flip to the new path.

BEGIN;

CREATE TABLE IF NOT EXISTS llm_shadow_diffs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    daemon TEXT NOT NULL,
    pipeline_stage TEXT,
    operation TEXT,
    tier TEXT NOT NULL,
    old_backend TEXT NOT NULL DEFAULT 'anthropic_direct',
    new_backend TEXT NOT NULL DEFAULT 'litellm_proxy',
    prompt_hash TEXT NOT NULL,
    prompt_preview TEXT,                 -- First 200 chars of prompt
    old_response TEXT,
    new_response TEXT,
    old_cost_usd NUMERIC(12, 8),
    new_cost_usd NUMERIC(12, 8),
    cost_delta_usd NUMERIC(12, 8),       -- new - old (negative = savings)
    old_latency_ms INTEGER,
    new_latency_ms INTEGER,
    latency_delta_ms INTEGER,
    old_length INTEGER,
    new_length INTEGER,
    length_diff_pct NUMERIC(5, 2),
    diff_similarity NUMERIC(5, 4),       -- 0-1, cosine similarity via embedding
    significant_diff BOOLEAN NOT NULL DEFAULT FALSE,  -- True if similarity < 0.85
    new_backend_failed BOOLEAN NOT NULL DEFAULT FALSE,
    new_backend_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_shadow_timestamp ON llm_shadow_diffs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_daemon ON llm_shadow_diffs(daemon, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_significant ON llm_shadow_diffs(significant_diff, timestamp DESC) WHERE significant_diff = true;
CREATE INDEX IF NOT EXISTS idx_shadow_failures ON llm_shadow_diffs(new_backend_failed, timestamp DESC) WHERE new_backend_failed = true;

-- Aggregate view for the War Room dashboard
CREATE OR REPLACE VIEW shadow_diff_summary AS
SELECT
    DATE(timestamp) AS date,
    daemon,
    tier,
    COUNT(*) AS total_compared,
    SUM(CASE WHEN significant_diff THEN 1 ELSE 0 END) AS significant_diffs,
    SUM(CASE WHEN significant_diff THEN 1 ELSE 0 END)::FLOAT / GREATEST(COUNT(*), 1) AS significant_diff_rate,
    SUM(CASE WHEN new_backend_failed THEN 1 ELSE 0 END) AS new_backend_failures,
    AVG(diff_similarity) AS avg_similarity,
    AVG(cost_delta_usd) AS avg_cost_delta_usd,
    SUM(cost_delta_usd) AS total_cost_delta_usd,
    AVG(latency_delta_ms) AS avg_latency_delta_ms,
    AVG(old_latency_ms) AS avg_old_latency_ms,
    AVG(new_latency_ms) AS avg_new_latency_ms
FROM llm_shadow_diffs
GROUP BY DATE(timestamp), daemon, tier;

COMMIT;
