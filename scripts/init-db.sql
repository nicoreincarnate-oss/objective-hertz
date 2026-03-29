-- PERSEUS V17 Database Schema
-- Auto-loaded on first Postgres container start

CREATE TABLE IF NOT EXISTS clients (
    id SERIAL PRIMARY KEY,
    business_name VARCHAR(255) NOT NULL,
    contact_name VARCHAR(255) NOT NULL DEFAULT '',
    email VARCHAR(255) NOT NULL DEFAULT '',
    phone VARCHAR(50),
    industry VARCHAR(100),
    website_url VARCHAR(500),
    status VARCHAR(50) DEFAULT 'discovered',
    source_campaign VARCHAR(255),
    source VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
-- Dedupe: one lead per email address (when email is set)
CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_email_unique
    ON clients (email) WHERE email IS NOT NULL AND email != '';
-- Dedupe: one lead per (name, source) when email is missing
CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_name_source_unique
    ON clients (business_name, source) WHERE email IS NULL OR email = '';

CREATE TABLE IF NOT EXISTS deals (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    product VARCHAR(50) NOT NULL CHECK (product IN ('website', 'hosting', 'receptionist')),
    amount DECIMAL(10,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD',
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'refunded', 'cancelled')),
    paid_at TIMESTAMP,
    wise_reference VARCHAR(255),
    payment_url VARCHAR(500),
    payment_provider VARCHAR(50),
    payment_link_id VARCHAR(255),
    payment_instructions TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hosting_subscriptions (
    id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    domain VARCHAR(255),
    netlify_site_id VARCHAR(255),
    deploy_url VARCHAR(500),
    monthly_price DECIMAL(10,2) DEFAULT 52.00,
    status VARCHAR(50) DEFAULT 'active' CHECK (status IN ('active', 'paused', 'cancelled')),
    next_billing_date DATE,
    cancelled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
-- One hosting subscription per client — required for ON CONFLICT (client_id) upserts
CREATE UNIQUE INDEX IF NOT EXISTS idx_hosting_sub_client_unique
    ON hosting_subscriptions (client_id);

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
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    pipeline_stage VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS budget_recurring_costs (
    category VARCHAR(100) PRIMARY KEY,
    monthly_amount DECIMAL(10,2) NOT NULL,
    description TEXT,
    active BOOLEAN DEFAULT TRUE,
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
-- Payment artifact persistence: store provider-specific details needed to
-- resend, recover, or reconcile invoices across Stripe/Wise/Conway.
ALTER TABLE deals ADD COLUMN IF NOT EXISTS payment_url VARCHAR(500);
ALTER TABLE deals ADD COLUMN IF NOT EXISTS payment_provider VARCHAR(50);
ALTER TABLE deals ADD COLUMN IF NOT EXISTS payment_link_id VARCHAR(255);
ALTER TABLE deals ADD COLUMN IF NOT EXISTS payment_instructions TEXT;

CREATE INDEX IF NOT EXISTS idx_deals_client ON deals(client_id);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
CREATE INDEX IF NOT EXISTS idx_deals_paid ON deals(paid_at);
CREATE INDEX IF NOT EXISTS idx_outreach_date ON outreach_metrics(date);
CREATE INDEX IF NOT EXISTS idx_outreach_domain ON outreach_metrics(domain);
CREATE INDEX IF NOT EXISTS idx_budget_month ON budget_tracking(month);
CREATE INDEX IF NOT EXISTS idx_budget_category ON budget_tracking(category);
CREATE INDEX IF NOT EXISTS idx_budget_client ON budget_tracking(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_budget_recurring_active ON budget_recurring_costs(active);
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

INSERT INTO budget_recurring_costs (category, monthly_amount, description, active) VALUES
    ('instantly_subscription', 97.00, 'Instantly.ai monthly subscription', TRUE)
ON CONFLICT (category) DO NOTHING;

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
ALTER TABLE clients ADD COLUMN IF NOT EXISTS research_facts JSONB DEFAULT '{}';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS research_graph JSONB DEFAULT '{}';
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
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'dead_letter')),
    retry_count INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 5,
    assigned_agent VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status);
CREATE INDEX IF NOT EXISTS idx_task_queue_type ON task_queue(task_type);
-- Fast dedupe lookup: is there already a pending/running task of this type?
CREATE INDEX IF NOT EXISTS idx_task_queue_dedupe ON task_queue(task_type, status)
    WHERE status IN ('pending', 'running');
ALTER TABLE task_queue ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;
ALTER TABLE task_queue DROP CONSTRAINT IF EXISTS task_queue_status_check;
DO $$ BEGIN
    ALTER TABLE task_queue ADD CONSTRAINT task_queue_status_check
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'dead_letter'));
EXCEPTION WHEN others THEN NULL;
END $$;

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
    is_customized BOOLEAN NOT NULL DEFAULT FALSE,
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
    client_id INTEGER REFERENCES clients(id),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_learnings_category ON titan_learnings(category);
CREATE INDEX IF NOT EXISTS idx_learnings_confidence ON titan_learnings(confidence);
-- Client-scoped retrieval: get_relevant_learnings filters on client_id
ALTER TABLE titan_learnings ADD COLUMN IF NOT EXISTS client_id INTEGER REFERENCES clients(id);
CREATE INDEX IF NOT EXISTS idx_learnings_client_id ON titan_learnings(client_id) WHERE client_id IS NOT NULL;

-- Revenue expansion opportunities: Perseus only expands when ROI is positive.
CREATE TABLE IF NOT EXISTS revenue_expansion_opportunities (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    stage VARCHAR(100) NOT NULL,
    bottleneck VARCHAR(255) NOT NULL,
    capability_type VARCHAR(50) NOT NULL
        CHECK (capability_type IN ('skill', 'prompt', 'tool', 'agent')),
    capability_name VARCHAR(255) NOT NULL,
    target_metric VARCHAR(100) NOT NULL,
    success_metric VARCHAR(255) NOT NULL,
    expected_monthly_revenue_gain DECIMAL(10,2) NOT NULL DEFAULT 0,
    expected_monthly_cost DECIMAL(10,2) NOT NULL DEFAULT 0,
    expected_roi FLOAT NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'shadow', 'adopted', 'rejected', 'rolled_back')),
    shadow_percent INTEGER NOT NULL DEFAULT 10,
    reasoning TEXT,
    smallest_step TEXT,
    rollback_condition TEXT,
    observed_summary JSONB DEFAULT '{}',
    proposal JSONB DEFAULT '{}',
    shadow_started_at TIMESTAMP,
    decided_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_revenue_expansion_status ON revenue_expansion_opportunities(status);
CREATE INDEX IF NOT EXISTS idx_revenue_expansion_stage ON revenue_expansion_opportunities(stage);
CREATE INDEX IF NOT EXISTS idx_revenue_expansion_capability ON revenue_expansion_opportunities(capability_name);

-- Titan rules: data-proven behavioral constraints injected into prompts
-- Unlike titan_learnings (suggestions), rules are deterministic and measured.
CREATE TABLE IF NOT EXISTS titan_rules (
    id SERIAL PRIMARY KEY,
    category VARCHAR(100) NOT NULL,
    rule_text TEXT NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_before FLOAT,
    metric_after FLOAT,
    sample_size INTEGER DEFAULT 0,
    confidence FLOAT DEFAULT 0.0,
    active BOOLEAN DEFAULT TRUE,
    source_learning_id INTEGER REFERENCES titan_learnings(id),
    created_at TIMESTAMP DEFAULT NOW(),
    evaluated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_titan_rules_category ON titan_rules(category);
CREATE INDEX IF NOT EXISTS idx_titan_rules_active ON titan_rules(active) WHERE active = TRUE;

-- Agent decisions: auditable trail of autonomous decisions across all agents
CREATE TABLE IF NOT EXISTS agent_decisions (
    id SERIAL PRIMARY KEY,
    agent VARCHAR(100) NOT NULL,
    decision_type VARCHAR(100) NOT NULL,
    context JSONB NOT NULL DEFAULT '{}',
    decision JSONB NOT NULL DEFAULT '{}',
    reasoning TEXT,
    outcome JSONB DEFAULT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_agent ON agent_decisions(agent);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_type ON agent_decisions(decision_type);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_created ON agent_decisions(created_at);

-- Sleep cycle log: nightly contrarian review audit trail
CREATE TABLE IF NOT EXISTS sleep_cycle_log (
    id SERIAL PRIMARY KEY,
    cycle_date DATE NOT NULL UNIQUE,
    system_snapshot JSONB NOT NULL DEFAULT '{}',
    alpha_proposals JSONB NOT NULL DEFAULT '{}',
    beta_verdicts JSONB NOT NULL DEFAULT '{}',
    applied_changes JSONB NOT NULL DEFAULT '[]',
    rolled_back BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

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
        CHECK (status IN ('pending', 'queued', 'sent', 'opened', 'replied', 'bounced')),
    simulation_status VARCHAR(20) DEFAULT 'pending'
        CHECK (simulation_status IN ('pending', 'passed', 'flagged')),
    simulation_score INTEGER,
    simulation_summary TEXT,
    simulation_personas JSONB DEFAULT '[]',
    simulation_checked_at TIMESTAMP,
    sent_at TIMESTAMP,
    opened_at TIMESTAMP,
    replied_at TIMESTAMP,
    smartlead_id VARCHAR(255),
    instantly_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_seq_client ON email_sequences(client_id);
CREATE INDEX IF NOT EXISTS idx_email_seq_status ON email_sequences(status);
-- Dedupe: one email per (client, step) — prevents duplicate compose/follow-up
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_seq_client_step_unique
    ON email_sequences (client_id, step);

-- Outbound email log: immutable record of every email dispatched
-- soul/soul_copy.md line 41: "Log every send — no exceptions"
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
INSERT INTO system_config (key, value, is_customized) VALUES
    ('review_mode', 'true', FALSE),
    ('sales_completed', '0', FALSE),
    ('sales_before_autonomy', '10', FALSE),
    ('email_daily_target', '1000', FALSE),
    ('warm_up_phase', 'true', FALSE),
    ('expansion_enabled', 'true', FALSE),
    ('expansion_monthly_budget', '50', FALSE),
    ('expansion_min_expected_roi', '1.5', FALSE),
    ('expansion_shadow_percent', '10', FALSE),
    ('expansion_shadow_min_sample', '10', FALSE),
    ('active_shadow_discovery_skill', '""', FALSE),
    ('active_shadow_opportunity_id', '0', FALSE),
    ('preferred_discovery_skill', '""', FALSE),
    ('warmup_day', '1', FALSE),
    ('paused_domains', '[]', FALSE),
    ('company_address', '"[SET YOUR PHYSICAL ADDRESS]"', FALSE),
    ('unsubscribe_base_url', '"https://your-domain.com"', FALSE),
    ('shadow_mode', 'true', FALSE)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    is_customized = FALSE,
    updated_at = NOW()
WHERE system_config.is_customized = FALSE;

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
FROM (
    SELECT month, amount FROM budget_tracking
    UNION ALL
    SELECT DATE_TRUNC('month', CURRENT_DATE)::date AS month, monthly_amount AS amount
    FROM budget_recurring_costs
    WHERE active = TRUE
) budget_sources
GROUP BY month
ORDER BY month DESC;

CREATE OR REPLACE VIEW v_effective_budget_tracking AS
SELECT month, category, amount, description, created_at
FROM budget_tracking
UNION ALL
SELECT
    DATE_TRUNC('month', CURRENT_DATE)::date AS month,
    category,
    monthly_amount AS amount,
    description,
    created_at
FROM budget_recurring_costs
WHERE active = TRUE;

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

-- Cost per acquisition: how much we spent (API, sending) per lead that reached each stage
CREATE OR REPLACE VIEW v_cost_per_lead AS
SELECT
    c.id AS client_id,
    c.business_name,
    c.status,
    c.lead_score,
    COALESCE(SUM(bt.amount), 0) AS total_cost,
    COUNT(bt.id) AS cost_entries
FROM clients c
LEFT JOIN budget_tracking bt ON bt.client_id = c.id
GROUP BY c.id, c.business_name, c.status, c.lead_score
ORDER BY total_cost DESC;

CREATE OR REPLACE VIEW v_cost_per_stage AS
SELECT
    pipeline_stage,
    COUNT(*) AS api_calls,
    SUM(amount) AS total_cost,
    ROUND(AVG(amount)::numeric, 4) AS avg_cost_per_call
FROM budget_tracking
WHERE pipeline_stage IS NOT NULL
GROUP BY pipeline_stage
ORDER BY total_cost DESC;

-- MAGMA causal edge audit trail (tracks Neo4j causal inference quality in Postgres)
CREATE TABLE IF NOT EXISTS magma_causal_audit (
    id SERIAL PRIMARY KEY,
    node_id TEXT NOT NULL,
    cause_node_id TEXT,
    effect_node_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    validated BOOLEAN DEFAULT FALSE,
    validated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_causal_audit_node ON magma_causal_audit(node_id);
CREATE INDEX IF NOT EXISTS idx_causal_audit_confidence ON magma_causal_audit(confidence);

-- Source quality: conversion funnel per discovery source
CREATE OR REPLACE VIEW v_source_quality AS
SELECT
    COALESCE(NULLIF(c.source, ''), 'unknown') AS source,
    COUNT(*) AS total_leads,
    COUNT(*) FILTER (WHERE c.email IS NOT NULL AND c.email != '') AS has_email,
    COUNT(*) FILTER (WHERE c.status NOT IN ('discovered', 'lost')) AS progressed,
    COUNT(*) FILTER (WHERE c.status IN ('replied', 'interested', 'demo_built', 'proposal_sent', 'negotiating', 'closed', 'building', 'deployed', 'invoiced', 'paid')) AS engaged,
    COUNT(*) FILTER (WHERE c.status IN ('closed', 'building', 'deployed', 'invoiced', 'paid')) AS converted,
    COUNT(*) FILTER (WHERE c.status = 'paid') AS paid,
    COALESCE(SUM(CASE WHEN d.status = 'paid' THEN d.amount ELSE 0 END), 0) AS revenue,
    COALESCE(SUM(bt.amount), 0) AS total_cost,
    CASE
        WHEN COUNT(*) > 0
        THEN ROUND(COUNT(*) FILTER (WHERE c.status IN ('closed', 'building', 'deployed', 'invoiced', 'paid'))::numeric / COUNT(*) * 100, 2)
        ELSE 0
    END AS conversion_rate_pct
FROM clients c
LEFT JOIN deals d ON d.client_id = c.id
LEFT JOIN budget_tracking bt ON bt.client_id = c.id
GROUP BY COALESCE(NULLIF(source, ''), 'unknown')
ORDER BY converted DESC, total_leads DESC;
