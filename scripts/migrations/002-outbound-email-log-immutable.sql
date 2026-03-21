-- Migration 002: Make outbound_email_log append-only
-- For existing installs, run manually:
--   psql -U perseus -d perseus -f scripts/migrations/002-outbound-email-log-immutable.sql

CREATE OR REPLACE FUNCTION prevent_outbound_email_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Outbound email log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_outbound_email_log_immutable ON outbound_email_log;
CREATE TRIGGER trg_outbound_email_log_immutable
BEFORE UPDATE OR DELETE ON outbound_email_log
FOR EACH ROW
EXECUTE FUNCTION prevent_outbound_email_log_mutation();
