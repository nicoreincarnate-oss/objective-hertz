"""Schema regression tests for audit-log protections."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_init_schema_makes_outbound_email_log_controlled_lifecycle_only():
    sql = (ROOT / "scripts" / "init-db.sql").read_text()

    assert "CREATE OR REPLACE FUNCTION prevent_outbound_email_log_mutation()" in sql
    assert "send_status VARCHAR(20) NOT NULL DEFAULT 'pending'" in sql
    assert "immutable except pending send finalization" in sql
    assert "BEFORE UPDATE OR DELETE ON outbound_email_log" in sql


def test_migration_adds_outbound_email_log_mutation_guard():
    sql = (ROOT / "scripts" / "migrations" / "002-outbound-email-log-immutable.sql").read_text()

    assert "CREATE OR REPLACE FUNCTION prevent_outbound_email_log_mutation()" in sql
    assert "CREATE TRIGGER trg_outbound_email_log_immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON outbound_email_log" in sql


def test_status_migration_allows_pending_send_finalization_only():
    sql = (ROOT / "scripts" / "migrations" / "005-outbound-email-log-status.sql").read_text()

    assert "ADD COLUMN IF NOT EXISTS send_status" in sql
    assert "ADD COLUMN IF NOT EXISTS sent_at" in sql
    assert "AND OLD.send_status = 'pending'" in sql
    assert "AND NEW.send_status IN ('sent', 'failed')" in sql
