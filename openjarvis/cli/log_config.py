"""Global logging configuration for the OpenJarvis CLI."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(
    verbose: bool = False,
    quiet: bool = False,
    log_file: str | Path | None = None,
) -> logging.Logger:
    """Configure the ``openjarvis`` logger.

    Parameters
    ----------
    verbose:
        Set log level to DEBUG.
    quiet:
        Set log level to ERROR (overrides verbose if both set).
    log_file:
        Path for a rotating file handler.  Only adds a file handler
        when an explicit path is provided (no implicit defaults).

    Returns
    -------
    The configured ``openjarvis`` logger.
    """
    logger = logging.getLogger("openjarvis")

    # Clear existing handlers to avoid duplication across calls
    logger.handlers.clear()

    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    fmt = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # File handler (only when an explicit path is given — never auto-create
    # files under the user's home directory, which breaks test hermeticity)
    if log_file is not None:
        file_handler = RotatingFileHandler(
            str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3,
        )
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        file_handler.setFormatter(file_fmt)
        logger.addHandler(file_handler)

    return logger
