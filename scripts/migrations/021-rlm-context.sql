-- Migration 021: Add mem0_context_id to clients table for RLM context linking
-- Phase 5: RLM Recursive Context Retrieval
-- Links leads to their Mem0 research context for vector retrieval

ALTER TABLE clients ADD COLUMN IF NOT EXISTS mem0_context_id TEXT;

COMMENT ON COLUMN clients.mem0_context_id IS 'Mem0 context ID linking to stored research for RLM retrieval';
