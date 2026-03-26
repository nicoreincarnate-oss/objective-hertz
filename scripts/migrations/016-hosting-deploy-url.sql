-- Migration 016: Fix hosting_subscriptions for Netlify deploy bookkeeping.
--
-- Problem: clawdbot/netlify_deploy.py writes deploy_url and uses
-- ON CONFLICT (client_id), but the table has neither the column nor
-- the unique constraint. Every deploy upsert fails silently.

ALTER TABLE hosting_subscriptions
    ADD COLUMN IF NOT EXISTS deploy_url VARCHAR(500);

CREATE UNIQUE INDEX IF NOT EXISTS idx_hosting_sub_client_unique
    ON hosting_subscriptions (client_id);
