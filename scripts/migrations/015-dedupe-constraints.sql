-- Migration 015: Add uniqueness constraints to prevent race-condition duplicates.
--
-- Problem: lead_discovery.py and email_compose.py do read-then-insert dedup
-- in application code. Two overlapping workers can both read "no duplicate"
-- and both insert, minting duplicate leads and duplicate emails.
--
-- Fix: enforce uniqueness at the database level. The application-level checks
-- remain as an optimization (avoid the INSERT attempt), but the DB is the
-- enforcement layer.

-- 1. Clients: unique on non-empty email.
--    Partial index: only enforces uniqueness when email is actually set.
--    Leads without email are deduplicated by (business_name, source) instead.

-- Ensure the source column exists before creating the dedupe index on it.
-- On fresh bootstrap init-db.sql includes it in CREATE TABLE, but on
-- older databases the column may not exist yet.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS source VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_email_unique
    ON clients (email)
    WHERE email IS NOT NULL AND email != '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_name_source_unique
    ON clients (business_name, source)
    WHERE email IS NULL OR email = '';

-- 2. Email sequences: one email per (client, step).
--    Prevents compose_emails or process_follow_ups from creating duplicate
--    step entries when two workers process the same lead concurrently.
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_seq_client_step_unique
    ON email_sequences (client_id, step);
