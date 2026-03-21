"""
Structured logging for all Perseus agents.
"""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from shared.config import config


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for machine-readable logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(agent_name: str = "perseus") -> logging.Logger:
    """Set up logging for a Perseus agent with rotation and optional JSON output."""
    config.log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"perseus.{agent_name}")
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    logger.propagate = False

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    text_formatter = logging.Formatter(
        fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    json_formatter = JsonFormatter()
    formatter = json_formatter if config.log_format.lower() == "json" else text_formatter

    if config.log_to_stdout:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        logger.addHandler(console)

    file_handler = RotatingFileHandler(
        config.log_dir / f"{agent_name}.log",
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
