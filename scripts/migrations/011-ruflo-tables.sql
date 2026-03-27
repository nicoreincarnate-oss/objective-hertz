-- Migration 011: Ruflo integration tables
-- Tracks Ruflo swarm tasks, learned patterns, and file import graph for validation.

-- ── Ruflo task tracking ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ruflo_tasks (
    id SERIAL PRIMARY KEY,
    task_type VARCHAR(100) NOT NULL,
    source VARCHAR(50) NOT NULL,
    source_id VARCHAR(100),
    finding JSONB DEFAULT '{}',
    swarm_config JSONB DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'pending'
        CHECK (status IN ('pending', 'dispatched', 'running', 'completed', 'failed', 'validated', 'rejected', 'cancelled')),
    result JSONB DEFAULT '{}',
    diff_text TEXT,
    test_results JSONB DEFAULT '{}',
    confidence FLOAT DEFAULT 0.0,
    validation_status VARCHAR(50)
        CHECK (validation_status IN ('passed', 'failed', 'skipped') OR validation_status IS NULL),
    validation_details JSONB DEFAULT '{}',
    ollama_tokens_used INTEGER DEFAULT 0,
    claude_tokens_used INTEGER DEFAULT 0,
    claude_cost DECIMAL(10,4) DEFAULT 0,
    duration_seconds INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    validated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ruflo_tasks_status ON ruflo_tasks(status);
CREATE INDEX IF NOT EXISTS idx_ruflo_tasks_source ON ruflo_tasks(source);
CREATE INDEX IF NOT EXISTS idx_ruflo_tasks_type ON ruflo_tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_ruflo_tasks_created ON ruflo_tasks(created_at DESC);

-- ── Ruflo learned patterns ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS ruflo_patterns (
    id SERIAL PRIMARY KEY,
    pattern_name VARCHAR(255) NOT NULL,
    pattern_type VARCHAR(100) NOT NULL,
    description TEXT,
    file_glob VARCHAR(255),
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    confidence FLOAT DEFAULT 0.5,
    last_used_at TIMESTAMP,
    trajectory_data JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(pattern_name, pattern_type)
);

CREATE INDEX IF NOT EXISTS idx_ruflo_patterns_type ON ruflo_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_ruflo_patterns_confidence ON ruflo_patterns(confidence DESC);

-- ── File import graph (for validation: who imports what) ─────────
CREATE TABLE IF NOT EXISTS file_import_graph (
    id SERIAL PRIMARY KEY,
    file_path VARCHAR(500) NOT NULL UNIQUE,
    imports JSONB DEFAULT '[]',
    imported_by JSONB DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_file_import_graph_path ON file_import_graph(file_path);

-- ── Dedup guard for Ruflo memory sync ────────────────────────────
-- Prevents duplicate titan_learnings when the background sync runs
-- concurrently with the post-validation learning write.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'titan_learnings' AND column_name = 'source_id'
    ) THEN
        ALTER TABLE titan_learnings ADD COLUMN source_id VARCHAR(100);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_titan_learnings_ruflo_source
    ON titan_learnings (source_id)
    WHERE source_event LIKE 'ruflo_%';
