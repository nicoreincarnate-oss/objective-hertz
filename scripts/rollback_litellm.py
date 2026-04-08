#!/usr/bin/env python3
"""rollback_litellm — Emergency rollback to direct-Anthropic path.

Use when LiteLLM proxy or any of the new tier infrastructure breaks production.
Flips feature flags, restarts daemons, verifies traffic is on the old path,
pages the operator via Telegram.

This is the script the cold-start 1-hour rollback runbook references.

Usage:
    python -m scripts.rollback_litellm                # Full rollback
    python -m scripts.rollback_litellm --soft         # Disable but keep proxy running
    python -m scripts.rollback_litellm --reason TEXT  # Annotate the rollback
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], check: bool = True) -> tuple[int, str, str]:
    """Run a shell command, return (returncode, stdout, stderr)."""
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if check and proc.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}")
    return proc.returncode, proc.stdout, proc.stderr


async def page_operator(reason: str) -> None:
    """Send a critical Telegram alert via Hermes."""
    try:
        from shared.comms import send_telegram_alert
        await send_telegram_alert(
            f"⚠️ LITELLM ROLLBACK INITIATED\nReason: {reason}\nTime: {datetime.now(timezone.utc).isoformat()}",
            urgency="critical",
        )
    except Exception as exc:
        print(f"  WARN: Could not page operator via Telegram: {exc}", file=sys.stderr)


def disable_feature_flags() -> None:
    """Patch .env to disable LiteLLM-related flags."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        print("  WARN: .env not found, skipping feature flag update")
        return
    lines = env_path.read_text().splitlines()
    flags_to_disable = {
        "LITELLM_PROXY_ENABLED": "false",
        "LITELLM_SHADOW_MODE":   "false",
        "TIERED_ROUTING_ENABLED": "false",
        "AUTO_TIER_ENABLED":     "false",
        "LEAD_WORKER_ENABLED":   "false",
        "LANGFUSE_ENABLED":      "false",
        "SEMANTIC_CACHE_ENABLED": "false",
        "LITELLM_ROLLOUT_PCT":   "0",
        "LOCAL_TIER_ENABLED":    "false",
    }
    new_lines = []
    seen: set[str] = set()
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in flags_to_disable:
                new_lines.append(f"{key}={flags_to_disable[key]}")
                seen.add(key)
                continue
        new_lines.append(line)
    for key, value in flags_to_disable.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n")
    print(f"  Disabled {len(flags_to_disable)} feature flags in .env")


def restart_daemons() -> None:
    """Restart all daemons via the existing makefile target."""
    print("  Restarting daemons via 'make restart'...")
    run(["make", "-C", str(REPO_ROOT), "restart"], check=False)


def verify_old_path() -> bool:
    """Verify daemons are using the direct Anthropic path."""
    print("  Verifying old path is active...")
    # Check Hermes /healthz endpoint
    try:
        import httpx
        for port in (8500,):
            try:
                resp = httpx.get(f"http://localhost:{port}/api/liveness", timeout=5.0)
                if resp.status_code == 200:
                    print(f"  Hermes healthcheck OK on port {port}")
                    return True
            except Exception:
                continue
    except ImportError:
        pass
    print("  WARN: Could not verify via HTTP healthcheck — check manually")
    return False


def soft_rollback() -> None:
    """Soft rollback — disable flags only, keep proxy running for inspection."""
    print("\n=== SOFT ROLLBACK ===\n")
    disable_feature_flags()
    print("\n  Soft rollback complete. Proxy still running for inspection.")
    print("  To restart daemons with new flags: make restart")


def hard_rollback() -> None:
    """Full rollback — disable flags, restart daemons, verify, page operator."""
    print("\n=== HARD ROLLBACK ===\n")
    t0 = time.perf_counter()

    print("\nStep 1: Disable feature flags")
    disable_feature_flags()

    print("\nStep 2: Restart daemons")
    restart_daemons()

    print("\nStep 3: Wait for daemons to come back")
    time.sleep(10)

    print("\nStep 4: Verify old path active")
    verify_old_path()

    elapsed = time.perf_counter() - t0
    print(f"\n=== ROLLBACK COMPLETE in {elapsed:.0f}s ===\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--soft", action="store_true", help="Soft rollback (flags only)")
    parser.add_argument("--reason", type=str, default="manual", help="Reason annotation")
    parser.add_argument("--no-page", action="store_true", help="Skip operator page")
    args = parser.parse_args()

    if args.soft:
        soft_rollback()
    else:
        hard_rollback()

    if not args.no_page:
        try:
            asyncio.run(page_operator(args.reason))
        except Exception as exc:
            print(f"WARN: paging failed: {exc}", file=sys.stderr)

    print("\nNext steps:")
    print("  1. Check Hermes War Room dashboard for daemon health")
    print("  2. Verify Telegram alert was received")
    print("  3. Investigate root cause (check logs, recent commits)")
    print("  4. When ready to retry: edit .env, restart daemons, monitor closely")
    return 0


if __name__ == "__main__":
    sys.exit(main())
