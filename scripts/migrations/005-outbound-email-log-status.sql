-- Migration 005: pre-send outbound audit rows with controlled status finalization
-- For existing installs, run manually:
--   psql -U perseus -d perseus -f scripts/migrations/005-outbound-email-log-status.sql

ALTER TABLE outbound_email_log
ADD COLUMN IF NOT EXISTS send_status VARCHAR(20) NOT NULL DEFAULT 'pending';

ALTER TABLE outbound_email_log
ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP;

ALTER TABLE outbound_email_log
ADD COLUMN IF NOT EXISTS delivery_error TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'outbound_email_log_send_status_check'
    ) THEN
        ALTER TABLE outbound_email_log
        ADD CONSTRAINT outbound_email_log_send_status_check
        CHECK (send_status IN ('pending', 'sent', 'failed'));
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_outbound_log_status ON outbound_email_log(send_status);

CREATE OR REPLACE FUNCTION prevent_outbound_email_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Outbound email log does not allow deletes';
    END IF;

    IF OLD.client_id IS NOT DISTINCT FROM NEW.client_id
       AND OLD.email_sequence_id IS NOT DISTINCT FROM NEW.email_sequence_id
       AND OLD.recipient_email IS NOT DISTINCT FROM NEW.recipient_email
       AND OLD.subject IS NOT DISTINCT FROM NEW.subject
       AND OLD.body IS NOT DISTINCT FROM NEW.body
       AND OLD.campaign_id IS NOT DISTINCT FROM NEW.campaign_id
       AND OLD.compliance_checks IS NOT DISTINCT FROM NEW.compliance_checks
       AND OLD.created_at IS NOT DISTINCT FROM NEW.created_at
       AND OLD.send_status = 'pending'
       AND NEW.send_status IN ('sent', 'failed') THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'Outbound email log is immutable except pending send finalization';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_outbound_email_log_immutable ON outbound_email_log;
CREATE TRIGGER trg_outbound_email_log_immutable
BEFORE UPDATE OR DELETE ON outbound_email_log
FOR EACH ROW
EXECUTE FUNCTION prevent_outbound_email_log_mutation();
