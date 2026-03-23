-- Conway Terminal: Economic nervous system tables
-- Provides wallet tracking, transaction ledger, and compute instance management

-- Agent wallets on Base L2
CREATE TABLE IF NOT EXISTS conway_wallets (
    agent_name  TEXT PRIMARY KEY,
    chain       TEXT NOT NULL DEFAULT 'base',
    public_address TEXT NOT NULL,
    keystore_ref   TEXT NOT NULL,  -- reference to encrypted keystore file
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conway_wallets_address
    ON conway_wallets (public_address);

-- Economic ledger: every financial transaction
CREATE TABLE IF NOT EXISTS conway_ledger (
    id          SERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    tx_type     TEXT NOT NULL CHECK (tx_type IN (
        'spend', 'earn', 'transfer', 'fund',
        'compute_rental', 'inference', 'service'
    )),
    amount      DECIMAL(18,8) NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'USDC',
    counterparty TEXT DEFAULT '',
    description TEXT DEFAULT '',
    tx_hash     TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conway_ledger_agent
    ON conway_ledger (agent, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conway_ledger_type
    ON conway_ledger (tx_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conway_ledger_created
    ON conway_ledger (created_at DESC);

-- Active compute instances rented by agents
CREATE TABLE IF NOT EXISTS conway_compute_instances (
    id          SERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT 'conway',
    instance_id TEXT NOT NULL,
    gpu_type    TEXT,
    price_per_hour DECIMAL(10,4),
    started_at  TIMESTAMPTZ DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    status      TEXT DEFAULT 'active' CHECK (status IN ('active', 'stopped', 'terminated')),
    metadata    JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_conway_compute_agent
    ON conway_compute_instances (agent, status);
CREATE INDEX IF NOT EXISTS idx_conway_compute_status
    ON conway_compute_instances (status) WHERE status = 'active';

-- View: monthly Conway spending by agent (for BudgetGuard integration)
CREATE OR REPLACE VIEW v_conway_monthly_spending AS
SELECT
    agent,
    DATE_TRUNC('month', created_at) AS month,
    SUM(amount) AS total_spent,
    COUNT(*) AS tx_count
FROM conway_ledger
WHERE tx_type IN ('spend', 'compute_rental', 'inference', 'service')
GROUP BY agent, DATE_TRUNC('month', created_at);

-- View: active compute cost burn rate
CREATE OR REPLACE VIEW v_conway_compute_burn AS
SELECT
    agent,
    COUNT(*) AS active_instances,
    SUM(price_per_hour) AS hourly_burn,
    SUM(price_per_hour) * 24 AS daily_burn
FROM conway_compute_instances
WHERE status = 'active'
GROUP BY agent;
