#!/usr/bin/env python3
"""Escalation log rotation — P1-9.

Rotates the encrypted escalation log (JSONL-over-AES-GCM) when either:
  - the file exceeds 100 MB (size trigger), OR
  - the file is older than 7 days by mtime (age trigger).

Retention: keeps up to 10 rotations (escalation.log.1 .. escalation.log.10).
Older rotations are deleted.

Preserves the associated salt file. Any file under the log directory whose
name contains ".salt" is explicitly excluded from rotation/deletion so the
AES-256 key derivation continues to work across rollovers (P0-5 compatibility).

Usage:
    python3 scripts/rotate_escalation_log.py            # rotate if needed
    python3 scripts/rotate_escalation_log.py --dry-run  # log actions only

Intended to be invoked by launchd or cron (hourly or daily).

Stdlib-only: os, shutil, pathlib, argparse, time, sys.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

# Rotation thresholds (explicit constants for audit grep)
SIZE_THRESHOLD_BYTES = 100 * 1024 * 1024  # 100 MB
AGE_THRESHOLD_SECONDS = 7 * 24 * 60 * 60  # 7 days == 604800 seconds
MAX_ROTATIONS = 10
DEFAULT_LOG_PATH = "~/Library/Logs/perseus/escalation.log"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _is_salt_file(path: Path) -> bool:
    """Preserve salt files — they are required for AES-256 key derivation."""
    return ".salt" in path.name


def needs_rotation(log_path: Path, now: float | None = None) -> tuple[bool, str]:
    """Return (should_rotate, reason)."""
    if not log_path.exists():
        return False, "log file does not exist"
    try:
        stat = log_path.stat()
    except OSError as exc:
        return False, f"stat failed: {exc}"

    if stat.st_size >= SIZE_THRESHOLD_BYTES:
        return True, f"size {stat.st_size} >= {SIZE_THRESHOLD_BYTES} bytes"

    now = now if now is not None else time.time()
    age = now - stat.st_mtime
    if age >= AGE_THRESHOLD_SECONDS:
        return True, f"age {int(age)}s >= {AGE_THRESHOLD_SECONDS}s (7 days)"

    return False, f"size={stat.st_size} age={int(age)}s — no rotation needed"


def rotate(log_path: Path, dry_run: bool = False) -> int:
    """Perform the rotation. Returns 0 on success, 1 on error."""
    try:
        # Walk from oldest to newest: .10 gets deleted, .9 -> .10, ..., .1 -> .2
        oldest = log_path.with_name(f"{log_path.name}.{MAX_ROTATIONS}")
        if oldest.exists() and not _is_salt_file(oldest):
            _log(f"[rotate] deleting oldest rotation: {oldest}")
            if not dry_run:
                oldest.unlink()

        for i in range(MAX_ROTATIONS - 1, 0, -1):
            src = log_path.with_name(f"{log_path.name}.{i}")
            dst = log_path.with_name(f"{log_path.name}.{i + 1}")
            if src.exists() and not _is_salt_file(src):
                _log(f"[rotate] {src.name} -> {dst.name}")
                if not dry_run:
                    shutil.move(str(src), str(dst))

        # Current -> .1
        dst = log_path.with_name(f"{log_path.name}.1")
        _log(f"[rotate] {log_path.name} -> {dst.name}")
        if not dry_run:
            shutil.move(str(log_path), str(dst))

        # Explicitly verify salt files survived (belt-and-suspenders)
        if log_path.parent.exists():
            for entry in log_path.parent.iterdir():
                if _is_salt_file(entry):
                    _log(f"[rotate] preserved salt file: {entry.name}")

        return 0
    except OSError as exc:
        _log(f"[rotate] ERROR: {exc}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotate the Perseus escalation log.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without moving or deleting files.",
    )
    parser.add_argument(
        "--log-path",
        default=None,
        help="Override log path (else PERSEUS_ESCALATION_LOG_PATH env var).",
    )
    args = parser.parse_args(argv)

    raw_path = (
        args.log_path
        or os.environ.get("PERSEUS_ESCALATION_LOG_PATH")
        or DEFAULT_LOG_PATH
    )
    log_path = Path(os.path.expanduser(raw_path))

    _log(f"[rotate] log_path={log_path} dry_run={args.dry_run}")

    should, reason = needs_rotation(log_path)
    _log(f"[rotate] decision: should_rotate={should} reason={reason}")

    if not should:
        return 0

    return rotate(log_path, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
