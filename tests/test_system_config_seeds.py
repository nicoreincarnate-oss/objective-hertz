"""Static checks for system_config seed ownership."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_init_db_tracks_seed_owned_system_config():
    code = (ROOT / "scripts/init-db.sql").read_text()
    assert "is_customized BOOLEAN NOT NULL DEFAULT FALSE" in code
    assert "INSERT INTO system_config (key, value, is_customized) VALUES" in code
    assert "WHERE system_config.is_customized = FALSE" in code


def test_set_config_marks_values_as_customized():
    code = (ROOT / "shared/db.py").read_text()
    assert "is_customized = TRUE" in code


def test_existing_installs_have_migration_for_seed_ownership():
    code = (ROOT / "scripts/migrations/004-system-config-seed-ownership.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS is_customized" in code
    assert "WHERE system_config.is_customized = FALSE" in code
