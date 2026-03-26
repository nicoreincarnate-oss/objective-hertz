"""Behavioral tests for Perseus structured logging setup.

Tests actual logging behavior: formatter output, handler wiring,
JSON vs text modes, stdout control.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch, tmp_path):
    """Provide a fake config so logging_config loads without .env."""
    cfg = MagicMock()
    cfg.log_dir = tmp_path / "logs"
    cfg.log_level = "DEBUG"
    cfg.log_format = "text"
    cfg.log_to_stdout = False
    cfg.log_max_bytes = 1024
    cfg.log_backup_count = 1
    cfg.observability.environment = "test"
    monkeypatch.setattr("shared.logging_config.config", cfg)
    monkeypatch.setattr("shared.observability.config", cfg)
    yield cfg


@pytest.fixture()
def _fresh_logger():
    """Remove any cached handlers between tests."""
    yield
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("perseus."):
            lgr = logging.getLogger(name)
            lgr.handlers.clear()


class TestSetupLogging:
    """setup_logging must wire real handlers and formatters."""

    def test_creates_log_directory(self, _fresh_logger, _patch_config):
        from shared.logging_config import setup_logging

        logger = setup_logging("titan")
        assert _patch_config.log_dir.exists()

    def test_creates_file_handler(self, _fresh_logger, _patch_config):
        from shared.logging_config import setup_logging

        logger = setup_logging("titan")
        file_handlers = [h for h in logger.handlers if hasattr(h, "baseFilename")]
        assert len(file_handlers) == 1
        assert "titan.log" in file_handlers[0].baseFilename

    def test_no_stdout_when_disabled(self, _fresh_logger, _patch_config):
        _patch_config.log_to_stdout = False
        from shared.logging_config import setup_logging

        logger = setup_logging("hermes")
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                          and not hasattr(h, "baseFilename")]
        assert len(stream_handlers) == 0

    def test_stdout_when_enabled(self, _fresh_logger, _patch_config):
        _patch_config.log_to_stdout = True
        from shared.logging_config import setup_logging

        logger = setup_logging("hermes_stdout")
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                          and not hasattr(h, "baseFilename")]
        assert len(stream_handlers) == 1

    def test_idempotent_handler_setup(self, _fresh_logger, _patch_config):
        from shared.logging_config import setup_logging

        logger1 = setup_logging("clawdbot")
        count1 = len(logger1.handlers)
        logger2 = setup_logging("clawdbot")
        assert len(logger2.handlers) == count1, "calling setup_logging twice should not double handlers"


class TestJsonFormatter:
    """JsonFormatter must produce valid JSON with required fields."""

    def test_output_is_valid_json(self, _patch_config):
        from shared.logging_config import JsonFormatter

        fmt = JsonFormatter("titan")
        record = logging.LogRecord(
            name="perseus.titan", level=logging.INFO, pathname="",
            lineno=0, msg="test message", args=(), exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["service"] == "titan"
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "test message"

    def test_includes_exception_when_present(self, _patch_config):
        from shared.logging_config import JsonFormatter

        fmt = JsonFormatter("titan")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="perseus.titan", level=logging.ERROR, pathname="",
            lineno=0, msg="error", args=(), exc_info=exc_info,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "boom" in parsed["exception"]

    def test_json_mode_uses_json_formatter(self, _fresh_logger, _patch_config):
        _patch_config.log_format = "json"
        from shared.logging_config import JsonFormatter, setup_logging

        logger = setup_logging("json_test")
        formatters = [h.formatter for h in logger.handlers]
        assert any(isinstance(f, JsonFormatter) for f in formatters)
