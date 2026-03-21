-- Migration 004: allow default config seeds to evolve without overwriting real settings
-- For existing installs, run manually:
--   psql -U perseus -d perseus -f scripts/migrations/004-system-config-seed-ownership.sql

ALTER TABLE system_config
ADD COLUMN IF NOT EXISTS is_customized BOOLEAN NOT NULL DEFAULT FALSE;

-- Existing rows that still match shipped defaults remain seed-owned.
-- Everything else is treated as customized and will not be overwritten by reruns.
UPDATE system_config
SET is_customized = CASE
    WHEN key = 'review_mode' AND value = 'true'::jsonb THEN FALSE
    WHEN key = 'sales_completed' AND value = '0'::jsonb THEN FALSE
    WHEN key = 'sales_before_autonomy' AND value = '10'::jsonb THEN FALSE
    WHEN key = 'email_daily_target' AND value = '1000'::jsonb THEN FALSE
    WHEN key = 'warm_up_phase' AND value = 'true'::jsonb THEN FALSE
    WHEN key = 'company_address' AND value = '"[SET YOUR PHYSICAL ADDRESS]"'::jsonb THEN FALSE
    WHEN key = 'unsubscribe_base_url' AND value = '"https://your-domain.com"'::jsonb THEN FALSE
    ELSE TRUE
END;

INSERT INTO system_config (key, value, is_customized) VALUES
    ('review_mode', 'true', FALSE),
    ('sales_completed', '0', FALSE),
    ('sales_before_autonomy', '10', FALSE),
    ('email_daily_target', '1000', FALSE),
    ('warm_up_phase', 'true', FALSE),
    ('company_address', '"[SET YOUR PHYSICAL ADDRESS]"', FALSE),
    ('unsubscribe_base_url', '"https://your-domain.com"', FALSE)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    is_customized = FALSE,
    updated_at = NOW()
WHERE system_config.is_customized = FALSE;
