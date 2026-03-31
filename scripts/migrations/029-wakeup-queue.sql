-- Migration 029: Event-Driven Wakeup Queue (Phase 16)
-- Adds wakeup subscription and request tables + NOTIFY trigger on events table.
-- Feature flag: EVENT_WAKEUP_ENABLED (default OFF -- tables exist but are not queried)

BEGIN;

-- Wakeup Subscriptions
-- Each agent registers interest in event patterns. When a matching event
-- fires, a wakeup request is created for that agent.

CREATE TABLE IF NOT EXISTS wakeup_subscriptions (
    subscription_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        VARCHAR(50) NOT NULL,
    event_pattern   VARCHAR(200) NOT NULL,
    priority        INTEGER DEFAULT 0,
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent_id, event_pattern)
);

CREATE INDEX IF NOT EXISTS idx_wakeup_sub_agent
    ON wakeup_subscriptions (agent_id) WHERE enabled = TRUE;

CREATE INDEX IF NOT EXISTS idx_wakeup_sub_pattern
    ON wakeup_subscriptions (event_pattern) WHERE enabled = TRUE;

COMMENT ON TABLE wakeup_subscriptions IS
    'Agent event subscriptions for wakeup queue (Phase 16 Paperclip)';
COMMENT ON COLUMN wakeup_subscriptions.event_pattern IS
    'Exact event_type match or SQL LIKE pattern (e.g. pipeline_% for all pipeline events)';
COMMENT ON COLUMN wakeup_subscriptions.priority IS
    'Higher priority subscriptions produce higher-priority wakeup requests (0=normal, 10=critical)';

-- Wakeup Requests
-- Created when an event matches a subscription, or by timer/manual trigger.
-- The WakeupQueue dispatches pending requests to sleeping agents.

CREATE TABLE IF NOT EXISTS wakeup_requests (
    request_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        VARCHAR(50) NOT NULL,
    source          VARCHAR(20) NOT NULL DEFAULT 'event'
                    CHECK (source IN ('timer', 'assignment', 'event', 'on_demand')),
    event_type      VARCHAR(100),
    idempotency_key VARCHAR(200) UNIQUE,
    coalesced_count INTEGER DEFAULT 1,
    context         JSONB DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'dispatched', 'expired', 'logged')),
    priority        INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    dispatched_at   TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '5 minutes'
);

CREATE INDEX IF NOT EXISTS idx_wakeup_req_agent_status
    ON wakeup_requests (agent_id, status) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_wakeup_req_created
    ON wakeup_requests (created_at);

CREATE INDEX IF NOT EXISTS idx_wakeup_req_expires
    ON wakeup_requests (expires_at) WHERE status = 'pending';

COMMENT ON TABLE wakeup_requests IS
    'Pending agent wakeup requests -- dispatched by WakeupQueue (Phase 16 Paperclip)';
COMMENT ON COLUMN wakeup_requests.idempotency_key IS
    'Prevents duplicate wakeups: agent_id:event_type:minute_bucket for event-sourced, agent_id:timer:minute for timer';
COMMENT ON COLUMN wakeup_requests.coalesced_count IS
    'Number of duplicate events that were merged into this single wakeup request';
COMMENT ON COLUMN wakeup_requests.status IS
    'logged = shadow mode only (event recorded but not dispatched)';

-- NOTIFY Trigger
-- After every INSERT on the events table, fire a Postgres NOTIFY on
-- channel "wakeup_events" with the event_type as payload.
-- The WakeupQueue LISTEN thread picks this up and creates wakeup_requests
-- for matching subscriptions.

CREATE OR REPLACE FUNCTION notify_wakeup_event()
RETURNS TRIGGER AS $$
BEGIN
    -- Fire on the general wakeup channel with event_type as payload.
    -- The listener parses the payload and matches against subscriptions.
    PERFORM pg_notify('wakeup_events', NEW.event_type || ':' || NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Only fire on INSERT (new events). Updates/deletes don't generate wakeups.
DROP TRIGGER IF EXISTS events_notify_wakeup ON events;
CREATE TRIGGER events_notify_wakeup
    AFTER INSERT ON events
    FOR EACH ROW
    EXECUTE FUNCTION notify_wakeup_event();

COMMENT ON FUNCTION notify_wakeup_event() IS
    'Fires pg_notify on wakeup_events channel after every event INSERT (Phase 16)';

-- Default Subscriptions
-- Seed event subscriptions for the 4 daemons + orchestrator.
-- Uses ON CONFLICT DO NOTHING so migration is idempotent.

-- Titan: revenue pipeline events
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('titan', 'new_lead', 5),
    ('titan', 'pipeline_stage_complete', 7),
    ('titan', 'email_sent', 3),
    ('titan', 'follow_up_due', 8),
    ('titan', 'lead_research_complete', 5),
    ('titan', 'lead_enrichment_complete', 4),
    ('titan', 'site_build_complete', 6),
    ('titan', 'payment_received', 10)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

-- Hermes: alerts and health events
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('hermes', 'alert_triggered', 9),
    ('hermes', 'health_check_failed', 10),
    ('hermes', 'approval_requested', 8),
    ('hermes', 'budget_exceeded', 10),
    ('hermes', 'budget_warning', 7),
    ('hermes', 'urgent_alert', 10),
    ('hermes', 'agent_help_request', 6)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

-- ClawdBot: site building and browser tasks
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('clawdbot', 'site_build_requested', 8),
    ('clawdbot', 'skill_execution_requested', 7),
    ('clawdbot', 'site_verify_requested', 5),
    ('clawdbot', 'browser_task_queued', 6)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

-- Perseus (orchestrator): subscribes to ALL high-value events
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('perseus', 'budget_exceeded', 10),
    ('perseus', 'health_check_failed', 10),
    ('perseus', 'pipeline_stage_complete', 5),
    ('perseus', 'payment_received', 9),
    ('perseus', 'agent_help_request', 7),
    ('perseus', 'vassal_crash', 10),
    ('perseus', 'budget_warning', 6)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

COMMIT;
