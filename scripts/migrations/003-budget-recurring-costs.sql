-- Migration 003: Add recurring monthly budget costs and effective budget view
-- For existing installs, run manually:
--   psql -U perseus -d perseus -f scripts/migrations/003-budget-recurring-costs.sql

CREATE TABLE IF NOT EXISTS budget_recurring_costs (
    category VARCHAR(100) PRIMARY KEY,
    monthly_amount DECIMAL(10,2) NOT NULL,
    description TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_budget_recurring_active ON budget_recurring_costs(active);

INSERT INTO budget_recurring_costs (category, monthly_amount, description, active) VALUES
    ('instantly_subscription', 97.00, 'Instantly.ai monthly subscription', TRUE)
ON CONFLICT (category) DO NOTHING;

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
