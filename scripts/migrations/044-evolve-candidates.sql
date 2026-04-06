-- Phase 32: AlphaEvolve Self-Improvement
-- Evolution candidate population and rollback/promotion log

-- Evolution candidate population
CREATE TABLE IF NOT EXISTS evolve_candidates (
    id VARCHAR(64) PRIMARY KEY,
    experiment_id VARCHAR(128) NOT NULL,
    artifact_type VARCHAR(32) NOT NULL,  -- prompt | email_template | scoring_rubric | pipeline_param
    parent_id VARCHAR(64),
    generation INT DEFAULT 0,
    island INT DEFAULT 0,
    content TEXT NOT NULL,
    metrics JSONB DEFAULT '{}',
    status VARCHAR(16) DEFAULT 'active',  -- active | deployed | promoted | rolled_back | pruned
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_evolve_experiment ON evolve_candidates(experiment_id, generation);
CREATE INDEX IF NOT EXISTS idx_evolve_type ON evolve_candidates(artifact_type, status);
CREATE INDEX IF NOT EXISTS idx_evolve_parent ON evolve_candidates(parent_id);

-- Evolution rollback/promotion log
CREATE TABLE IF NOT EXISTS evolve_rollbacks (
    id SERIAL PRIMARY KEY,
    experiment_id VARCHAR(128) NOT NULL,
    candidate_id VARCHAR(64) NOT NULL,
    action VARCHAR(16) NOT NULL,       -- rollback | promotion
    reason TEXT,
    metric_name VARCHAR(64),
    baseline_value REAL,
    current_value REAL,
    trial_count INT,
    hours_elapsed REAL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rollback_experiment ON evolve_rollbacks(experiment_id);
CREATE INDEX IF NOT EXISTS idx_rollback_action ON evolve_rollbacks(action, created_at);

-- Feature flag
INSERT INTO system_config (key, value)
VALUES ('ALPHA_EVOLVE', '"true"'::jsonb)
ON CONFLICT (key) DO NOTHING;
