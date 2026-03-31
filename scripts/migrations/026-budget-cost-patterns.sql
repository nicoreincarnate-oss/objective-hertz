-- Migration 026: Budget & Cost Patterns (Paperclip patterns 6, 7, 8)
-- Phase 13: Pre-execution budget gate, multi-scope policies, per-call cost events

-- =====================================================================
-- Table 1: budget_policies (Pattern 7 — Multi-Scope Budget Policies)
-- =====================================================================
CREATE TABLE IF NOT EXISTS budget_policies (
    policy_id SERIAL PRIMARY KEY,
    scope_type VARCHAR(50) NOT NULL CHECK (scope_type IN ('company', 'agent', 'pipeline_stage')),
    scope_value VARCHAR(200) NOT NULL,
    window_kind VARCHAR(20) NOT NULL CHECK (window_kind IN ('monthly', 'lifetime')),
    limit_usd DECIMAL(10,2) NOT NULL,
    warn_percent INTEGER NOT NULL DEFAULT 80 CHECK (warn_percent BETWEEN 1 AND 99),
    hard_stop_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(scope_type, scope_value, window_kind)
);

-- Seed default company policy: $800/month, warn at 80%, hard stop enabled
INSERT INTO budget_policies (scope_type, scope_value, window_kind, limit_usd, warn_percent, hard_stop_enabled, description)
VALUES ('company', 'objective_hertz', 'monthly', 800.00, 80, TRUE, 'Company-wide monthly budget cap')
ON CONFLICT (scope_type, scope_value, window_kind) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_budget_policies_scope ON budget_policies(scope_type, scope_value);

-- =====================================================================
-- Table 2: cost_events (Pattern 8 — Per-Call Cost Events)
-- =====================================================================
CREATE TABLE IF NOT EXISTS cost_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) NOT NULL,
    task_id VARCHAR(200),
    task_type VARCHAR(100),
    model VARCHAR(100) NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd DECIMAL(10,6) NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cost_events_agent_created ON cost_events(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cost_events_task ON cost_events(task_id) WHERE task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cost_events_created ON cost_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cost_events_model ON cost_events(model, created_at DESC);
