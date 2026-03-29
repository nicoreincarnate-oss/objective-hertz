-- Migration 020: stage_metrics table for middleware telemetry (Phase 4)
-- Tracks per-stage timing, success, and cost data for pipeline observability.

CREATE TABLE IF NOT EXISTS stage_metrics (
    id              SERIAL PRIMARY KEY,
    pipeline        TEXT NOT NULL,
    stage           TEXT NOT NULL,
    daemon          TEXT NOT NULL,
    duration_ms     INTEGER,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        DECIMAL(10, 6),
    success         BOOLEAN DEFAULT TRUE,
    middleware_overhead_ms INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stage_metrics_pipeline
    ON stage_metrics (pipeline, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_stage_metrics_stage
    ON stage_metrics (stage, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_stage_metrics_daemon
    ON stage_metrics (daemon, created_at DESC);
