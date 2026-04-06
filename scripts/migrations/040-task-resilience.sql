-- Migration 040: Task Resilience + Synthesis (Phase 24)
-- Adds synthesis cycle tracking and A2A message dedup log tables.
-- Feature flags: ANATOMY_TASK_RESILIENCE, ANATOMY_SYNTHESIS_CYCLE

BEGIN;

-- Allow 'continued' status in task_queue for re-entrant tasks
-- (task_queue.status is VARCHAR, no CHECK constraint to modify -- just document)
COMMENT ON TABLE task_queue IS
    'Task queue with statuses: pending, running, completed, failed, continued';

-- Synthesis cycle tracking
CREATE TABLE IF NOT EXISTS synthesis_cycles (
    cycle_id        VARCHAR(50) PRIMARY KEY,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    vassal_count    INTEGER DEFAULT 0,
    instructions    INTEGER DEFAULT 0,
    tokens_used     INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0.0,
    used_llm        BOOLEAN DEFAULT FALSE,
    mode            VARCHAR(20) DEFAULT 'full',
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_synthesis_cycles_started
    ON synthesis_cycles (started_at DESC);

COMMENT ON TABLE synthesis_cycles IS
    'Audit log of orchestrator synthesis cycles (Phase 24)';

-- Message deduplication tracking (for observability, not the in-memory set)
-- The BoundedUUIDSet is in-memory; this table is for cross-restart dedup seed.
CREATE TABLE IF NOT EXISTS a2a_message_log (
    message_id      VARCHAR(50) PRIMARY KEY,
    sender          VARCHAR(50),
    recipient       VARCHAR(50),
    protocol_type   VARCHAR(50),
    received_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_a2a_msg_log_received
    ON a2a_message_log (received_at DESC);

-- Auto-cleanup: remove entries older than 1 hour (run via periodic task)
COMMENT ON TABLE a2a_message_log IS
    'A2A message dedup log for cross-restart seed. Auto-pruned hourly.';

COMMIT;
