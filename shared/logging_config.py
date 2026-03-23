"""
Structured logging for all Perseus agents.
"""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from shared.config import config
from shared.observability import configure_service_observability, get_log_context

_STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for machine-readable logs."""

    def __init__(self, service_name: str):
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "service": self._service_name,
            "environment": config.observability.environment,
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
            "process": record.process,
            "thread": record.threadName,
        }
        payload.update(get_log_context())
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except TypeError:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(agent_name: str = "perseus") -> logging.Logger:
    """Set up logging for a Perseus agent with rotation and optional JSON output."""
    config.log_dir.mkdir(parents=True, exist_ok=True)
    configure_service_observability(agent_name)

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
    json_formatter = JsonFormatter(agent_name)
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
