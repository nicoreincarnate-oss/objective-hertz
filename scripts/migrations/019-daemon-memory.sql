-- Migration 019: daemon_memory table + stats view (Phase 3 — DeerFlow Persistent Memory)
-- Requirements: MEM-01, MEM-08

CREATE TABLE IF NOT EXISTS daemon_memory (
    id SERIAL PRIMARY KEY,
    daemon_name TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (memory_type IN ('episodic', 'semantic')),
    key TEXT NOT NULL,
    content JSONB NOT NULL,
    importance DECIMAL(3,2) DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    expires_at TIMESTAMPTZ,  -- NULL for semantic (permanent)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (daemon_name, memory_type, key)
);

-- Per-daemon row cap enforced at application level (10K per daemon)
CREATE INDEX IF NOT EXISTS idx_daemon_memory_daemon ON daemon_memory (daemon_name, memory_type);
CREATE INDEX IF NOT EXISTS idx_daemon_memory_expires ON daemon_memory (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_daemon_memory_importance ON daemon_memory (daemon_name, importance DESC);

-- Monitoring view (MEM-08)
CREATE OR REPLACE VIEW daemon_memory_stats AS
SELECT
    daemon_name,
    memory_type,
    COUNT(*) as row_count,
    AVG(importance) as avg_importance,
    MIN(created_at) as oldest_entry,
    MAX(updated_at) as newest_entry,
    SUM(CASE WHEN expires_at < NOW() THEN 1 ELSE 0 END) as expired_count
FROM daemon_memory
GROUP BY daemon_name, memory_type;
