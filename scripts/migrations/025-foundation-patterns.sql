-- Migration 025: Foundation Patterns (Phase 12)
-- All changes use DEFAULT values — safe for hot tables with zero downtime.

BEGIN;

-- Pattern 1: Agent State Machine
-- Add state tracking to agent_registry
ALTER TABLE agent_registry
    ADD COLUMN IF NOT EXISTS state VARCHAR(20) DEFAULT 'idle';
ALTER TABLE agent_registry
    ADD COLUMN IF NOT EXISTS pause_reason VARCHAR(20) DEFAULT NULL;

-- Pattern 3: Recursion Guard
-- Add depth tracking to task_queue
ALTER TABLE task_queue
    ADD COLUMN IF NOT EXISTS depth INTEGER DEFAULT 0;

-- Pattern 5: Session Health
-- Persistent session health snapshots
CREATE TABLE IF NOT EXISTS session_health (
    session_id UUID PRIMARY KEY,
    agent_id VARCHAR(100) NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for querying by agent and time
CREATE INDEX IF NOT EXISTS idx_session_health_agent_created
    ON session_health (agent_id, created_at DESC);

-- Seed feature flags (all OFF by default)
INSERT INTO system_config (key, value) VALUES
    ('AGENT_STATE_MACHINE_ENABLED', 'false'::jsonb),
    ('ATOMIC_CHECKOUT_ENABLED', 'false'::jsonb),
    ('RECURSION_GUARD_ENABLED', 'false'::jsonb),
    ('FORBIDDEN_TOKEN_SCAN_ENABLED', 'false'::jsonb),
    ('SESSION_HEALTH_ENABLED', 'false'::jsonb),
    ('max_task_depth', '5'::jsonb)
ON CONFLICT (key) DO NOTHING;

COMMIT;
