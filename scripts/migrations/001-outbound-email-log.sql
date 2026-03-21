-- Migration 001: Add outbound_email_log table + compliance config seeds
-- For existing installs, run manually:
--   psql -U perseus -d perseus -f scripts/migrations/001-outbound-email-log.sql
--
-- This is safe to re-run (IF NOT EXISTS / ON CONFLICT DO NOTHING).

CREATE TABLE IF NOT EXISTS outbound_email_log (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id),
    email_sequence_id INTEGER REFERENCES email_sequences(id),
    recipient_email VARCHAR(255) NOT NULL,
    subject VARCHAR(500),
    body TEXT,
    campaign_id VARCHAR(255),
    send_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (send_status IN ('pending', 'sent', 'failed')),
    sent_at TIMESTAMP,
    delivery_error TEXT,
    compliance_checks JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_outbound_log_client ON outbound_email_log(client_id);
CREATE INDEX IF NOT EXISTS idx_outbound_log_created ON outbound_email_log(created_at);
CREATE INDEX IF NOT EXISTS idx_outbound_log_status ON outbound_email_log(send_status);

INSERT INTO system_config (key, value) VALUES
    ('company_address', '"[SET YOUR PHYSICAL ADDRESS]"'),
    ('unsubscribe_base_url', '"https://your-domain.com"')
ON CONFLICT (key) DO NOTHING;
