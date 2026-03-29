-- Phase 2 Week 7: Bandit experiments + response latency
-- Papers: 93, 94, 99, 100 (bandit), 70 (response latency)

-- Bandit experiment tracking (Thompson Sampling + Track-and-Stop)
CREATE TABLE IF NOT EXISTS bandit_experiments (
    id SERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    arm_name TEXT NOT NULL,
    alpha REAL NOT NULL DEFAULT 1.0,
    beta REAL NOT NULL DEFAULT 1.0,
    total_pulls INT NOT NULL DEFAULT 0,
    converged BOOLEAN DEFAULT FALSE,
    winner TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(experiment_id, arm_name)
);
CREATE INDEX IF NOT EXISTS idx_bandit_experiment ON bandit_experiments(experiment_id);

-- Response latency tracking (hours between our contact and their reply)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS response_latency_hours FLOAT;

-- Milestone reward log (for analytics)
CREATE TABLE IF NOT EXISTS milestone_rewards (
    id SERIAL PRIMARY KEY,
    client_id INT REFERENCES clients(id),
    from_status TEXT,
    to_status TEXT,
    reward REAL NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_milestone_client ON milestone_rewards(client_id);
CREATE INDEX IF NOT EXISTS idx_milestone_reward ON milestone_rewards(reward);
