-- Phase 23: Unified LLM Client tables

-- Fallback event log -- records every time the fallback chain activates
CREATE TABLE IF NOT EXISTS llm_fallback_log (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tier_requested TEXT NOT NULL,
    tier_actual TEXT NOT NULL,
    fallback_depth INT NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    model_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    latency_ms INT,
    input_tokens INT,
    output_tokens INT,
    cost_usd NUMERIC(10, 6),
    pipeline_stage TEXT,
    daemon_name TEXT,
    success BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_fallback_log_created ON llm_fallback_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fallback_log_tier ON llm_fallback_log (tier_requested, tier_actual);

-- Shadow comparison results for 48h parallel run gate
CREATE TABLE IF NOT EXISTS llm_shadow_comparison (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tier TEXT NOT NULL,
    old_latency_ms INT,
    new_latency_ms INT,
    old_cost_usd NUMERIC(10, 6),
    new_cost_usd NUMERIC(10, 6),
    old_content_length INT,
    new_content_length INT,
    new_error TEXT,
    new_fallback_triggered BOOLEAN DEFAULT FALSE,
    new_fallback_depth INT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shadow_comparison_created ON llm_shadow_comparison (created_at DESC);
