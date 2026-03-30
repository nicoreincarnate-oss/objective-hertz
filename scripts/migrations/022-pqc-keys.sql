-- Migration 022: Post-Quantum Cryptography — encrypted_keys table
-- Phase 6: Dual-key support for quantum-safe key rotation
--
-- Stores encrypted keypairs per agent with dual-key overlap period.
-- Both classical and PQC keys work simultaneously for 30 days during
-- key rotation, then classical keys expire (valid_until set).
--
-- Key types:
--   'classical' — AES-256 only (legacy)
--   'pqc'       — ML-KEM-768 + ML-DSA-65 (when available)
--   'hybrid'    — AES-256-GCM + Ed25519 (fallback mode)

CREATE TABLE IF NOT EXISTS encrypted_keys (
    id SERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    key_type TEXT NOT NULL CHECK (key_type IN ('classical', 'pqc', 'hybrid')),
    public_key BYTEA NOT NULL,
    encrypted_private_key BYTEA NOT NULL,
    algorithm TEXT NOT NULL,  -- 'AES-256', 'ML-KEM-768', 'hybrid', 'ed25519+aes256gcm'
    valid_from TIMESTAMPTZ DEFAULT NOW(),
    valid_until TIMESTAMPTZ,  -- NULL = current active key, set date = deprecated
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent_name, key_type, valid_from)
);

-- Fast lookup: find active keys for an agent
CREATE INDEX IF NOT EXISTS idx_encrypted_keys_agent
    ON encrypted_keys (agent_name, key_type);

-- Find keys expiring in the next N days (for rotation alerts)
CREATE INDEX IF NOT EXISTS idx_encrypted_keys_expiry
    ON encrypted_keys (valid_until)
    WHERE valid_until IS NOT NULL;

-- Comment for documentation
COMMENT ON TABLE encrypted_keys IS
    'PQC dual-key store: per-agent encrypted keypairs with 30-day rotation overlap';
COMMENT ON COLUMN encrypted_keys.valid_until IS
    'NULL = current active key. Set to NOW()+30d during rotation for dual-key period';
COMMENT ON COLUMN encrypted_keys.key_type IS
    'classical = AES-256 only, pqc = ML-KEM-768, hybrid = fallback AES+Ed25519';
