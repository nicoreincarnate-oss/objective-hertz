CREATE TABLE IF NOT EXISTS revenue_expansion_opportunities (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    stage VARCHAR(100) NOT NULL,
    bottleneck VARCHAR(255) NOT NULL,
    capability_type VARCHAR(50) NOT NULL
        CHECK (capability_type IN ('skill', 'prompt', 'tool', 'agent')),
    capability_name VARCHAR(255) NOT NULL,
    target_metric VARCHAR(100) NOT NULL,
    success_metric VARCHAR(255) NOT NULL,
    expected_monthly_revenue_gain DECIMAL(10,2) NOT NULL DEFAULT 0,
    expected_monthly_cost DECIMAL(10,2) NOT NULL DEFAULT 0,
    expected_roi FLOAT NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'shadow', 'adopted', 'rejected', 'rolled_back')),
    shadow_percent INTEGER NOT NULL DEFAULT 10,
    reasoning TEXT,
    smallest_step TEXT,
    rollback_condition TEXT,
    observed_summary JSONB DEFAULT '{}',
    proposal JSONB DEFAULT '{}',
    shadow_started_at TIMESTAMP,
    decided_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_revenue_expansion_status ON revenue_expansion_opportunities(status);
CREATE INDEX IF NOT EXISTS idx_revenue_expansion_stage ON revenue_expansion_opportunities(stage);
CREATE INDEX IF NOT EXISTS idx_revenue_expansion_capability ON revenue_expansion_opportunities(capability_name);

INSERT INTO system_config (key, value, is_customized) VALUES
    ('expansion_enabled', 'true', FALSE),
    ('expansion_monthly_budget', '50', FALSE),
    ('expansion_min_expected_roi', '1.5', FALSE),
    ('expansion_shadow_percent', '10', FALSE),
    ('expansion_shadow_min_sample', '10', FALSE),
    ('active_shadow_discovery_skill', '""', FALSE),
    ('active_shadow_opportunity_id', '0', FALSE),
    ('preferred_discovery_skill', '""', FALSE)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    is_customized = FALSE,
    updated_at = NOW()
WHERE system_config.is_customized = FALSE;
