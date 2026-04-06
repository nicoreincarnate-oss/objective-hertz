-- Phase 29: Unified Memory Bus
-- MAGMA sync state, bandit persistence, memory provenance

-- MAGMA sync state for crash recovery (high-water mark per source table)
CREATE TABLE IF NOT EXISTS magma_sync_state (
    source_table VARCHAR(64) PRIMARY KEY,
    last_ingested_id BIGINT DEFAULT 0,
    last_ingested_at TIMESTAMPTZ DEFAULT NOW(),
    records_ingested BIGINT DEFAULT 0
);

-- Bandit state persistence (full experiment snapshot)
CREATE TABLE IF NOT EXISTS bandit_state (
    bandit_id VARCHAR(128) PRIMARY KEY,
    state_json JSONB NOT NULL,
    arm_count INT DEFAULT 0,
    total_pulls INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bandit_state_updated ON bandit_state(updated_at);

-- Memory provenance tracking (extends MAGMA nodes — tracked in Postgres for fast querying)
CREATE TABLE IF NOT EXISTS memory_provenance (
    magma_node_id VARCHAR(128) PRIMARY KEY,
    source_daemon VARCHAR(32) NOT NULL,
    source_table VARCHAR(64) NOT NULL,
    source_record_id VARCHAR(64) NOT NULL,
    visibility VARCHAR(16) DEFAULT 'public',
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_provenance_source ON memory_provenance(source_daemon, source_table);
CREATE INDEX IF NOT EXISTS idx_provenance_visibility ON memory_provenance(visibility);

-- Feature flags
INSERT INTO system_config (key, value) VALUES ('MEMORY_BUS_ENABLED', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO system_config (key, value) VALUES ('MEMORY_EXPLORER_ENABLED', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO system_config (key, value) VALUES ('BANDIT_PERSISTENCE', 'true') ON CONFLICT (key) DO NOTHING;
