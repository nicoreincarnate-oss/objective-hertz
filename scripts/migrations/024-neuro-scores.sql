-- Migration 024: Add neuro_scores JSONB column to email_sequences
-- Phase 8: TRIBE v2 Neuro-Scorer
--
-- Stores 4-dimension cognitive scores from the neuro-scorer:
--   {
--     "self_relevance": 0.72,
--     "trust": 0.68,
--     "cognitive_ease": 0.81,
--     "emotional_resonance": 0.65,
--     "composite": 0.71,
--     "dimension_weights": {"self_relevance": 0.30, ...},
--     "inference_mode": "fallback",
--     "latency_ms": 42.3
--   }

ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS neuro_scores JSONB;

-- Index on composite score for querying low-scoring emails
CREATE INDEX IF NOT EXISTS idx_email_sequences_neuro
    ON email_sequences ((neuro_scores->>'composite'))
    WHERE neuro_scores IS NOT NULL;

-- Index on inference mode for monitoring native vs fallback usage
CREATE INDEX IF NOT EXISTS idx_email_sequences_neuro_mode
    ON email_sequences ((neuro_scores->>'inference_mode'))
    WHERE neuro_scores IS NOT NULL;
