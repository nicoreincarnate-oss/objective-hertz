-- Migration 008: Memory & Learning System Upgrades (Phase 1)
-- Supports: prompt versioning, confidence decay, memory GC, contradiction detection

-- Enable trigram extension for memory dedup (memory_gc)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Prompt versioning: trace which prompt version produced which outcome
ALTER TABLE training_data ADD COLUMN IF NOT EXISTS prompt_version_hash TEXT;
ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS prompt_version_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_training_prompt_version
    ON training_data(prompt_version_hash) WHERE prompt_version_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_email_seq_prompt_version
    ON email_sequences(prompt_version_hash) WHERE prompt_version_hash IS NOT NULL;

-- Confidence decay tracking on rules
ALTER TABLE titan_rules ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMPTZ;

-- Pending outcomes for delayed training labels (Phase 2 prep)
CREATE TABLE IF NOT EXISTS pending_outcomes (
    id SERIAL PRIMARY KEY,
    training_data_id INT,
    client_id INT,
    email_seq_id INT,
    check_at TIMESTAMPTZ NOT NULL,
    check_type TEXT NOT NULL,
    checked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pending_outcomes_check
    ON pending_outcomes(check_at) WHERE NOT checked;

-- Prospect state column (Phase 4 prep)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS prospect_state JSONB DEFAULT '{}';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;

-- A/B testing metadata on email sequences (Phase 4 prep)
ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS ab_cohort TEXT;
ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS ab_variation TEXT;
