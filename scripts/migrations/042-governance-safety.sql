-- Phase 30: Governance + Safety Foundation
-- Migration 042: agent_audit_log + retrieval_sessions tables

-- Governance audit log
CREATE TABLE IF NOT EXISTS agent_audit_log (
    id SERIAL PRIMARY KEY,
    correlation_id VARCHAR(64),
    source_agent VARCHAR(32) NOT NULL,
    target_agent VARCHAR(32),
    action VARCHAR(128) NOT NULL,
    protocol VARCHAR(16) NOT NULL,  -- a2a | mcp | db | event_bus
    policy_result VARCHAR(16) NOT NULL,  -- allowed | denied | rate_limited
    cost_estimate REAL DEFAULT 0,
    duration_ms INT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON agent_audit_log(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_source_time ON agent_audit_log(source_agent, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_policy ON agent_audit_log(policy_result, created_at);

-- POMDP retrieval sessions
CREATE TABLE IF NOT EXISTS retrieval_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    query TEXT NOT NULL,
    steps INT DEFAULT 0,
    final_confidence REAL,
    poisoning_score REAL DEFAULT 0,
    flagged BOOLEAN DEFAULT FALSE,
    node_ids JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_retrieval_session ON retrieval_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_flagged ON retrieval_sessions(flagged) WHERE flagged = TRUE;

-- Feature flags for Phase 30
INSERT INTO system_config (key, value) VALUES ('MAS_GOVERNANCE', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO system_config (key, value) VALUES ('POMDP_SAFETY_BOUNDS', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO system_config (key, value) VALUES ('T2_MODEL_SELECT', 'true') ON CONFLICT (key) DO NOTHING;
