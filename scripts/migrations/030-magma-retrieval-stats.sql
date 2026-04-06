-- Migration 030: magma_retrieval_stats table for retrieval telemetry
-- Phase 19: Cost Measurement + Memory Telemetry
-- Feature flag: ANATOMY_COST_DASHBOARD

BEGIN;

CREATE TABLE IF NOT EXISTS magma_retrieval_stats (
    id              SERIAL PRIMARY KEY,
    query           TEXT NOT NULL,
    query_type      VARCHAR(100),               -- e.g. "email_compose", "lead_research"
    intent          VARCHAR(50),                 -- causal, temporal, entity, semantic
    anchors_found   INTEGER NOT NULL DEFAULT 0,  -- total anchors from RRF fusion
    anchors_used    INTEGER NOT NULL DEFAULT 0,  -- anchors that survived beam search
    confidence_avg  REAL,                        -- average confidence across used anchors
    confidence_max  REAL,                        -- max confidence (abstention threshold check)
    latency_ms      INTEGER NOT NULL DEFAULT 0,  -- total retrieval time
    decompose_ms    INTEGER,                     -- query decomposition time
    anchor_ms       INTEGER,                     -- anchor fusion time
    beam_ms         INTEGER,                     -- beam search time
    linearize_ms    INTEGER,                     -- linearization time
    result_tokens   INTEGER,                     -- approximate token count of linearized result
    abstained       BOOLEAN DEFAULT FALSE,       -- true if confidence below threshold
    cache_hit       BOOLEAN DEFAULT FALSE,       -- true if result served from cache
    client_id       INTEGER,                     -- client scope (NULL = global)
    daemon          VARCHAR(50),                 -- calling daemon name
    meta_params     JSONB,                       -- ALMA params used (beam_width, lambdas, etc.)
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Indexes for dashboard queries and ALMA feedback loop
CREATE INDEX IF NOT EXISTS idx_mrs_query_type ON magma_retrieval_stats(query_type);
CREATE INDEX IF NOT EXISTS idx_mrs_created_at ON magma_retrieval_stats(created_at);
CREATE INDEX IF NOT EXISTS idx_mrs_intent ON magma_retrieval_stats(intent);
CREATE INDEX IF NOT EXISTS idx_mrs_daemon ON magma_retrieval_stats(daemon);
CREATE INDEX IF NOT EXISTS idx_mrs_client_id ON magma_retrieval_stats(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mrs_abstained ON magma_retrieval_stats(abstained) WHERE abstained = TRUE;

-- Composite index for ALMA meta-learner feedback queries
CREATE INDEX IF NOT EXISTS idx_mrs_alma_feedback
    ON magma_retrieval_stats(query_type, created_at DESC)
    WHERE abstained = FALSE;

COMMIT;
