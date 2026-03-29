-- Phase 2 Week 10: Scientific method experiments
-- Paper: 96 (Scientific Method Loop)

CREATE TABLE IF NOT EXISTS experiments (
    id SERIAL PRIMARY KEY,
    hypothesis TEXT NOT NULL,
    change_description TEXT DEFAULT '',
    metric_name VARCHAR(100) NOT NULL,
    baseline_value FLOAT NOT NULL DEFAULT 0,
    expected_delta FLOAT NOT NULL DEFAULT 0,
    actual_delta FLOAT,
    cycle_id INT,
    status VARCHAR(20) DEFAULT 'active',  -- active, confirmed, refuted
    created_at TIMESTAMPTZ DEFAULT NOW(),
    evaluated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_experiments_metric ON experiments(metric_name);
