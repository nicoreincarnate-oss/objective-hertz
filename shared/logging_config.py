"""
Structured logging for all Perseus agents.
"""

import logging
import sys
from pathlib import Path

from shared.config import config


def setup_logging(agent_name: str = "perseus") -> logging.Logger:
    """Set up structured logging for a Perseus agent."""
    config.log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"perseus.{agent_name}")
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler
    file_handler = logging.FileHandler(config.log_dir / f"{agent_name}.log")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger
