-- Migration 028: Architecture Additive Patterns (Phase 15)
-- Three new tables + one ALTER. All additive — no existing tables modified beyond adding a column.
-- Safe for hot system with zero downtime.

BEGIN;

-- ============================================================
-- Pattern 12: Goal Cascade
-- ============================================================

CREATE TABLE IF NOT EXISTS goals (
    goal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level VARCHAR(20) NOT NULL CHECK (level IN ('company', 'team', 'agent', 'task')),
    parent_id UUID REFERENCES goals(goal_id) ON DELETE SET NULL,
    description TEXT NOT NULL,
    success_criteria TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'failed')),
    assigned_agent VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for goal queries
CREATE INDEX IF NOT EXISTS idx_goals_level ON goals(level);
CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(parent_id);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_agent ON goals(assigned_agent);

-- Lightweight goal tag on task_queue (NOT a foreign key)
ALTER TABLE task_queue
    ADD COLUMN IF NOT EXISTS goal_tag VARCHAR DEFAULT NULL;

-- Seed goals: company -> team -> agent hierarchy
INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, status)
VALUES (
    'a0000000-0000-0000-0000-000000000001',
    'company', NULL,
    'Generate $2K MRR from autonomous client acquisition',
    'Monthly recurring revenue >= $2000 from active client subscriptions',
    'active'
) ON CONFLICT DO NOTHING;

INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, status)
VALUES
    ('a0000000-0000-0000-0000-000000000010', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Build 10 client websites with full pipeline delivery',
     '10 sites deployed and verified live', 'active'),
    ('a0000000-0000-0000-0000-000000000011', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Maintain 95% pipeline task completion rate',
     'task_completion_rate >= 0.95 over rolling 30 days', 'active'),
    ('a0000000-0000-0000-0000-000000000012', 'team', 'a0000000-0000-0000-0000-000000000001',
     'Achieve <60s end-to-end pipeline latency',
     'p95 latency < 60000ms measured by observability', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO goals (goal_id, level, parent_id, description, success_criteria, assigned_agent, status)
VALUES
    ('a0000000-0000-0000-0000-000000000100', 'agent', 'a0000000-0000-0000-0000-000000000010',
     'Discover and qualify 50 leads per week',
     '50+ leads with status >= researched per 7-day window', 'titan', 'active'),
    ('a0000000-0000-0000-0000-000000000101', 'agent', 'a0000000-0000-0000-0000-000000000010',
     'Build and deploy client sites within 2h of deal close',
     'site_deploy_time < 7200s from close event', 'clawdbot', 'active'),
    ('a0000000-0000-0000-0000-000000000102', 'agent', 'a0000000-0000-0000-0000-000000000011',
     'Process all scheduled tasks within interval ceiling',
     'zero skipped non-skippable tasks per 24h', 'perseus', 'active'),
    ('a0000000-0000-0000-0000-000000000103', 'agent', 'a0000000-0000-0000-0000-000000000012',
     'Deliver alerts within 5 seconds of event',
     'p99 alert_delivery_latency < 5000ms', 'hermes', 'active')
ON CONFLICT DO NOTHING;

-- ============================================================
-- Pattern 13: Governance / Approval System
-- ============================================================

CREATE TABLE IF NOT EXISTS approvals (
    approval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(40) NOT NULL CHECK (type IN (
        'budget_override', 'autonomy_transition', 'config_change', 'capability_grant'
    )),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'approved', 'rejected', 'expired'
    )),
    requested_by VARCHAR(100) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    resolved_by VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_type ON approvals(type);
CREATE INDEX IF NOT EXISTS idx_approvals_expires ON approvals(expires_at) WHERE status = 'pending';

-- ============================================================
-- Pattern 14: Commit Metrics Tracker
-- ============================================================

CREATE TABLE IF NOT EXISTS commit_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commit_sha VARCHAR(40) UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    files_changed INTEGER NOT NULL DEFAULT 0,
    lines_added INTEGER NOT NULL DEFAULT 0,
    lines_removed INTEGER NOT NULL DEFAULT 0,
    co_authored BOOLEAN NOT NULL DEFAULT false,
    agent_id VARCHAR(100),
    session_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_commit_metrics_sha ON commit_metrics(commit_sha);
CREATE INDEX IF NOT EXISTS idx_commit_metrics_timestamp ON commit_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_commit_metrics_agent ON commit_metrics(agent_id);

-- ============================================================
-- Feature flags (all OFF by default)
-- ============================================================

INSERT INTO system_config (key, value) VALUES
    ('GOAL_CASCADE_ENABLED', 'false'::jsonb),
    ('GOVERNANCE_ENABLED', 'false'::jsonb),
    ('COMMIT_METRICS_ENABLED', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;

COMMIT;
