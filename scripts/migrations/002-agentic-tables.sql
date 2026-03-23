-- Migration 002: Agentic architecture tables (titan_rules, agent_decisions)
-- Safe to re-run (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS titan_rules (
    id SERIAL PRIMARY KEY,
    category VARCHAR(100) NOT NULL,
    rule_text TEXT NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_before FLOAT,
    metric_after FLOAT,
    sample_size INTEGER DEFAULT 0,
    confidence FLOAT DEFAULT 0.0,
    active BOOLEAN DEFAULT TRUE,
    source_learning_id INTEGER REFERENCES titan_learnings(id),
    created_at TIMESTAMP DEFAULT NOW(),
    evaluated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_titan_rules_category ON titan_rules(category);
CREATE INDEX IF NOT EXISTS idx_titan_rules_active ON titan_rules(active) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS agent_decisions (
    id SERIAL PRIMARY KEY,
    agent VARCHAR(100) NOT NULL,
    decision_type VARCHAR(100) NOT NULL,
    context JSONB NOT NULL DEFAULT '{}',
    decision JSONB NOT NULL DEFAULT '{}',
    reasoning TEXT,
    outcome JSONB DEFAULT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_agent ON agent_decisions(agent);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_type ON agent_decisions(decision_type);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_created ON agent_decisions(created_at);
