-- Migration 009: Hardening — write versioning, integrity checks, causal confidence
-- Addresses: cross-store rollback, write conflicts, causal edge quality

-- 1. Write versioning on titan_learnings — prevents concurrent overwrites
--    writer_agent tracks which agent wrote the row (for conflict detection)
--    write_version increments on every UPDATE (optimistic locking)
ALTER TABLE titan_learnings ADD COLUMN IF NOT EXISTS writer_agent TEXT DEFAULT '';
ALTER TABLE titan_learnings ADD COLUMN IF NOT EXISTS write_version INT DEFAULT 1;

-- 2. Write versioning on titan_rules
ALTER TABLE titan_rules ADD COLUMN IF NOT EXISTS writer_agent TEXT DEFAULT '';
ALTER TABLE titan_rules ADD COLUMN IF NOT EXISTS write_version INT DEFAULT 1;

-- 3. Write versioning on agent_decisions
ALTER TABLE agent_decisions ADD COLUMN IF NOT EXISTS write_version INT DEFAULT 1;

-- 4. Index for cross-store rollback: find learnings created during a cycle
CREATE INDEX IF NOT EXISTS idx_learnings_created_cat
    ON titan_learnings(created_at DESC, category);

-- 5. Causal edge confidence tracking — we can't ALTER Neo4j from SQL,
--    but we track causal inference quality in Postgres for auditing
CREATE TABLE IF NOT EXISTS magma_causal_audit (
    id SERIAL PRIMARY KEY,
    node_id TEXT NOT NULL,
    cause_node_id TEXT,
    effect_node_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence_count INT NOT NULL DEFAULT 1,
    inferred_at TIMESTAMPTZ DEFAULT NOW(),
    validated BOOLEAN DEFAULT FALSE,
    validated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_causal_audit_node ON magma_causal_audit(node_id);
CREATE INDEX IF NOT EXISTS idx_causal_audit_confidence ON magma_causal_audit(confidence);
