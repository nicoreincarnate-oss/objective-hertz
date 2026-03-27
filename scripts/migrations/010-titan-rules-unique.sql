-- Migration 010: Add UNIQUE constraint on titan_rules to prevent unbounded duplication
-- H-20: ON CONFLICT DO NOTHING requires a matching UNIQUE constraint

-- Step 1: Remove duplicates, keeping the newest row per (category, rule_text)
DELETE FROM titan_rules a
USING titan_rules b
WHERE a.id < b.id
  AND a.category = b.category
  AND a.rule_text = b.rule_text;

-- Step 2: Add the UNIQUE constraint
ALTER TABLE titan_rules
  ADD CONSTRAINT uq_titan_rules_category_rule UNIQUE (category, rule_text);
