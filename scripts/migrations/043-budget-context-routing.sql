-- Phase 31: Budget + Context + Routing Intelligence
-- BATS budget decisions, BACM compression stats, UGO utility scores

-- BATS budget decision log
CREATE TABLE IF NOT EXISTS budget_decisions (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(64),
    regime VARCHAR(16) NOT NULL,      -- HIGH | MEDIUM | LOW | CRITICAL
    token_spent REAL DEFAULT 0,
    token_budget REAL DEFAULT 0,
    tool_calls_made INT DEFAULT 0,
    tool_budget INT DEFAULT 0,
    constraint_triggered VARCHAR(32), -- per_call | hourly | daily | circuit_breaker | monthly | null
    action_taken VARCHAR(64),         -- proceed | downgrade_haiku | downgrade_ollama | reject
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_budget_task ON budget_decisions(task_id);
CREATE INDEX IF NOT EXISTS idx_budget_regime ON budget_decisions(regime, created_at);

-- BACM compression stats
CREATE TABLE IF NOT EXISTS compression_stats (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64),
    tokens_before INT NOT NULL,
    tokens_after INT NOT NULL,
    segments_total INT,
    segments_compressed INT,
    budget_ratio REAL,                -- remaining_tokens / context_window at time of compression
    compression_action VARCHAR(16),   -- null | partial | full
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_compression_session ON compression_stats(session_id);

-- UGO utility score log
CREATE TABLE IF NOT EXISTS utility_scores (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(64),
    step_number INT,
    action VARCHAR(32) NOT NULL,      -- respond | retrieve | tool_call | verify | stop
    gain REAL,
    step_cost REAL,
    uncertainty REAL,
    redundancy REAL,
    utility REAL,
    chosen BOOLEAN DEFAULT FALSE,
    budget_regime VARCHAR(16),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_utility_task ON utility_scores(task_id, step_number);
CREATE INDEX IF NOT EXISTS idx_utility_chosen ON utility_scores(chosen, created_at);

-- Feature flags for Phase 31
INSERT INTO system_config (key, value, description)
VALUES
    ('BATS_ADAPTIVE_BUDGET', 'true', 'Enable continuous budget tracking with regime-based behavioral adaptation'),
    ('BACM_COMPRESSION', 'true', 'Enable hierarchical importance-scored context compression'),
    ('UGO_UTILITY_ROUTING', 'true', 'Enable utility-guided action selection before each tool/agent call')
ON CONFLICT (key) DO NOTHING;
