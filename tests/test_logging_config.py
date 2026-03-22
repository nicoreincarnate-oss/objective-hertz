"""Static checks for production logging setup."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_logging_config_uses_rotation_and_optional_json():
    code = (ROOT / "shared/logging_config.py").read_text()
    assert "RotatingFileHandler" in code
    assert "JsonFormatter" in code
    assert "config.log_to_stdout" in code
    assert "config.log_format.lower() == \"json\"" in code


def test_config_exposes_logging_controls():
    code = (ROOT / "shared/config.py").read_text()
    assert 'log_format: str = _env("LOG_FORMAT", "text")' in code
    assert 'log_to_stdout: bool = _env_bool("LOG_TO_STDOUT", True)' in code
    assert 'log_max_bytes: int = _env_int("LOG_MAX_BYTES", 10485760)' in code
    assert 'log_backup_count: int = _env_int("LOG_BACKUP_COUNT", 5)' in code


def test_daemon_startup_disables_stdout_duplication():
    start_code = (ROOT / "scripts/start-perseus.sh").read_text()
    assert "LOG_TO_STDOUT=0" in start_code
    assert "nohup python3 -m perseus.daemon" in start_code
    assert "nohup python3 -m titan.daemon" in start_code
    assert "nohup python3 -m clawdbot.daemon" in start_code
    assert "python3 -m uvicorn hermes.web.app:app" in start_code

    for plist_name in [
        "com.perseus.master.plist",
        "com.perseus.titan.plist",
        "com.perseus.clawdbot.plist",
    ]:
        plist = (ROOT / "scripts/launchagents" / plist_name).read_text()
        assert "<key>LOG_TO_STDOUT</key>" in plist
