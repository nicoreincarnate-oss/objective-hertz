-- Phase 2 Week 8: Negotiation rounds
-- Papers: 71 (AgenticPay), 72 (Strategic Tradeoffs)

CREATE TABLE IF NOT EXISTS negotiation_rounds (
    id SERIAL PRIMARY KEY,
    client_id INT REFERENCES clients(id),
    round_number INT NOT NULL DEFAULT 1,
    our_offer JSONB DEFAULT '{}',
    their_response TEXT DEFAULT '',
    intent TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_negotiation_client ON negotiation_rounds(client_id);
CREATE INDEX IF NOT EXISTS idx_negotiation_round ON negotiation_rounds(client_id, round_number);
