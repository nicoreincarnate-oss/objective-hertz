-- Migration 017: LLM metrics observability table
-- Phase 0b: Async Engine + Interface Contracts + Observability

CREATE TABLE IF NOT EXISTS llm_metrics (
    id SERIAL PRIMARY KEY,
    daemon TEXT NOT NULL,
    model TEXT NOT NULL,
    call_type TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    cost_usd DECIMAL(10,6),
    success BOOLEAN DEFAULT TRUE,
    error_type TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_metrics_daemon ON llm_metrics (daemon, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_metrics_model ON llm_metrics (model, created_at DESC);
