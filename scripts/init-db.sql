-- PERSEUS V17 Database Schema
-- Auto-loaded on first Postgres container start

CREATE TABLE IF NOT EXISTS clients (
    id SERIAL PRIMARY KEY,
    business_name VARCHAR(255) NOT NULL,
    contact_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(50),
    industry VARCHAR(100),
    website_url VARCHAR(500),
    status VARCHAR(50) DEFAULT 'lead',
    source_campaign VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS deals (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    product VARCHAR(50) NOT NULL CHECK (product IN ('website', 'hosting', 'receptionist')),
    amount DECIMAL(10,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD',
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'refunded', 'cancelled')),
    paid_at TIMESTAMP,
    wise_reference VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hosting_subscriptions (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    domain VARCHAR(255),
    netlify_site_id VARCHAR(255),
    monthly_price DECIMAL(10,2) DEFAULT 52.00,
    status VARCHAR(50) DEFAULT 'active' CHECK (status IN ('active', 'paused', 'cancelled')),
    next_billing_date DATE,
    cancelled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS receptionist_subscriptions (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    goodcall_agent_id VARCHAR(255),
    monthly_price DECIMAL(10,2) DEFAULT 398.00,
    goodcall_cost DECIMAL(10,2) DEFAULT 79.00,
    status VARCHAR(50) DEFAULT 'trial' CHECK (status IN ('trial', 'active', 'paused', 'cancelled')),
    trial_start DATE,
    trial_end DATE,
    next_billing_date DATE,
    cancelled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outreach_metrics (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    domain VARCHAR(255) NOT NULL,
    campaign VARCHAR(255),
    emails_sent INTEGER DEFAULT 0,
    opens INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    bounces INTEGER DEFAULT 0,
    spam_complaints INTEGER DEFAULT 0,
    interested_replies INTEGER DEFAULT 0,
    unsubscribes INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(date, domain, campaign)
);

CREATE TABLE IF NOT EXISTS budget_tracking (
    id SERIAL PRIMARY KEY,
    month DATE NOT NULL,
    category VARCHAR(100) NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    description TEXT,
    receipt_url VARCHAR(500),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS site_health (
    id SERIAL PRIMARY KEY,
    hosting_id INTEGER REFERENCES hosting_subscriptions(id) ON DELETE CASCADE,
    checked_at TIMESTAMP DEFAULT NOW(),
    http_status INTEGER,
    response_time_ms INTEGER,
    ssl_valid BOOLEAN DEFAULT true,
    ssl_expiry DATE,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS activity_log (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(50) NOT NULL,
    entity_id INTEGER,
    action VARCHAR(100) NOT NULL,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_clients_status ON clients(status);
CREATE INDEX IF NOT EXISTS idx_clients_industry ON clients(industry);
CREATE INDEX IF NOT EXISTS idx_clients_email ON clients(email);
CREATE INDEX IF NOT EXISTS idx_deals_client ON deals(client_id);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
CREATE INDEX IF NOT EXISTS idx_deals_paid ON deals(paid_at);
CREATE INDEX IF NOT EXISTS idx_outreach_date ON outreach_metrics(date);
CREATE INDEX IF NOT EXISTS idx_outreach_domain ON outreach_metrics(domain);
CREATE INDEX IF NOT EXISTS idx_budget_month ON budget_tracking(month);
CREATE INDEX IF NOT EXISTS idx_budget_category ON budget_tracking(category);
CREATE INDEX IF NOT EXISTS idx_site_health_hosting ON site_health(hosting_id);
CREATE INDEX IF NOT EXISTS idx_site_health_checked ON site_health(checked_at);
CREATE INDEX IF NOT EXISTS idx_activity_entity ON activity_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);

-- Functions
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_clients_updated
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── PERSEUS NEW ARCHITECTURE TABLES ──────────────────────────────────

-- Pipeline state machine: expanded client statuses
ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_status_check;
DO $$ BEGIN
    ALTER TABLE clients ADD CONSTRAINT clients_status_check
        CHECK (status IN (
            'discovered', 'researched', 'email_drafted', 'email_queued',
            'email_sent', 'followed_up', 'replied', 'interested',
            'demo_built', 'proposal_sent', 'negotiating', 'closed',
            'building', 'deployed', 'invoiced', 'paid',
            'lost', 'unresponsive', 'unsubscribed', 'lead'
        ));
EXCEPTION WHEN others THEN NULL;
END $$;

-- Add new columns to clients for the full pipeline
ALTER TABLE clients ADD COLUMN IF NOT EXISTS country VARCHAR(100);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS city VARCHAR(255);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'en';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS research_summary TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS demo_site_url VARCHAR(500);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS final_site_url VARCHAR(500);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS lead_score FLOAT DEFAULT 0.0;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS source VARCHAR(255);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS last_contact_at TIMESTAMP;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS follow_up_count INTEGER DEFAULT 0;

-- Task queue: Perseus dispatches work to agents
CREATE TABLE IF NOT EXISTS task_queue (
    id SERIAL PRIMARY KEY,
    task_type VARCHAR(100) NOT NULL,
    payload JSONB DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    priority INTEGER DEFAULT 5,
    assigned_agent VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status);
CREATE INDEX IF NOT EXISTS idx_task_queue_type ON task_queue(task_type);

-- Events: agents emit events for Hermes/dashboard
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB DEFAULT '{}',
    acknowledged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_ack ON events(acknowledged);

-- Review queue: email drafts, proposals for Nico approval (first 10 sales)
CREATE TABLE IF NOT EXISTS review_queue (
    id SERIAL PRIMARY KEY,
    item_type VARCHAR(50) NOT NULL
        CHECK (item_type IN ('email_draft', 'proposal', 'invoice', 'demo_site', 'final_site')),
    client_id INTEGER REFERENCES clients(id),
    content JSONB NOT NULL,
    status VARCHAR(50) DEFAULT 'pending_review'
        CHECK (status IN ('pending_review', 'approved', 'rejected', 'sent')),
    reviewer_notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    reviewed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);

-- System config: runtime settings (review_mode, schedules, etc.)
CREATE TABLE IF NOT EXISTS system_config (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Titan learnings: what the system learns from every interaction
CREATE TABLE IF NOT EXISTS titan_learnings (
    id SERIAL PRIMARY KEY,
    category VARCHAR(100) NOT NULL,
    insight TEXT NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    source_lead_id INTEGER REFERENCES clients(id),
    source_event VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_learnings_category ON titan_learnings(category);
CREATE INDEX IF NOT EXISTS idx_learnings_confidence ON titan_learnings(confidence);

-- Agent registry: all agents register here
CREATE TABLE IF NOT EXISTS agent_registry (
    name VARCHAR(100) PRIMARY KEY,
    description TEXT,
    status VARCHAR(50) DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'error')),
    last_heartbeat TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Email sequences: track multi-step email campaigns per lead
CREATE TABLE IF NOT EXISTS email_sequences (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    step INTEGER DEFAULT 1,
    subject VARCHAR(500),
    body TEXT,
    status VARCHAR(50) DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'opened', 'replied', 'bounced')),
    sent_at TIMESTAMP,
    opened_at TIMESTAMP,
    replied_at TIMESTAMP,
    smartlead_id VARCHAR(255),
    instantly_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_seq_client ON email_sequences(client_id);
CREATE INDEX IF NOT EXISTS idx_email_seq_status ON email_sequences(status);

-- Training data: every interaction becomes a potential training example
CREATE TABLE IF NOT EXISTS training_data (
    id SERIAL PRIMARY KEY,
    example_type VARCHAR(100) NOT NULL,
    input_text TEXT NOT NULL,
    output_text TEXT NOT NULL,
    outcome VARCHAR(50) DEFAULT ''
        CHECK (outcome IN ('', 'positive', 'negative')),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_training_type ON training_data(example_type);
CREATE INDEX IF NOT EXISTS idx_training_outcome ON training_data(outcome);

-- Insert default system config
INSERT INTO system_config (key, value) VALUES
    ('review_mode', 'true'),
    ('sales_completed', '0'),
    ('sales_before_autonomy', '10'),
    ('email_daily_target', '1000'),
    ('warm_up_phase', 'true')
ON CONFLICT (key) DO NOTHING;

-- Views
CREATE OR REPLACE VIEW v_active_mrr AS
SELECT
    COALESCE(SUM(CASE WHEN h.status = 'active' THEN h.monthly_price ELSE 0 END), 0) AS hosting_mrr,
    COALESCE(SUM(CASE WHEN r.status = 'active' THEN r.monthly_price ELSE 0 END), 0) AS receptionist_mrr,
    COALESCE(SUM(CASE WHEN h.status = 'active' THEN h.monthly_price ELSE 0 END), 0) +
    COALESCE(SUM(CASE WHEN r.status = 'active' THEN r.monthly_price ELSE 0 END), 0) AS total_mrr
FROM hosting_subscriptions h
FULL OUTER JOIN receptionist_subscriptions r ON true;

CREATE OR REPLACE VIEW v_monthly_budget AS
SELECT
    month,
    SUM(amount) AS total_spent,
    800.00 - SUM(amount) AS remaining,
    ROUND(SUM(amount) / 800.00 * 100, 1) AS percent_used
FROM budget_tracking
GROUP BY month
ORDER BY month DESC;

CREATE OR REPLACE VIEW v_domain_health AS
SELECT
    domain,
    date,
    emails_sent,
    CASE WHEN emails_sent > 0 THEN ROUND(bounces::numeric / emails_sent * 100, 2) ELSE 0 END AS bounce_rate,
    CASE WHEN emails_sent > 0 THEN ROUND(opens::numeric / emails_sent * 100, 2) ELSE 0 END AS open_rate,
    CASE WHEN opens > 0 THEN ROUND(replies::numeric / opens * 100, 2) ELSE 0 END AS reply_rate,
    CASE
        WHEN emails_sent > 0 AND bounces::numeric / emails_sent > 0.05 THEN 'CRITICAL'
        WHEN emails_sent > 0 AND bounces::numeric / emails_sent > 0.03 THEN 'WARNING'
        ELSE 'OK'
    END AS health_status
FROM outreach_metrics
ORDER BY date DESC, domain;
