-- Migration 018: quality_scores table for anti-slop quality gate (Phase 2)
-- Stores per-content quality scores across 5 dimensions for trend analysis.

CREATE TABLE IF NOT EXISTS quality_scores (
    id SERIAL PRIMARY KEY,
    content_type TEXT NOT NULL,       -- 'email', 'site_copy', 'alert', 'internal'
    reference_id TEXT,                -- lead_id, site_id, alert_id, etc.
    clarity DECIMAL(4,3),
    specificity DECIMAL(4,3),
    authenticity DECIMAL(4,3),
    value_density DECIMAL(4,3),
    slop_score DECIMAL(4,3),
    composite DECIMAL(4,3),
    rewrite_count INTEGER DEFAULT 0,
    model_used TEXT DEFAULT 'haiku',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quality_scores_type
    ON quality_scores (content_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_quality_scores_ref
    ON quality_scores (reference_id);

CREATE INDEX IF NOT EXISTS idx_quality_scores_composite
    ON quality_scores (composite);

COMMENT ON TABLE quality_scores IS 'Anti-slop quality gate: per-content scores across 5 dimensions';
