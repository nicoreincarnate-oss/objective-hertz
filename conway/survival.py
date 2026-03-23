"""
Survival tier management for Perseus agents.

Monitors each agent's credit balance and adjusts capabilities:
- normal:      Full inference, 15s heartbeat
- low_compute: Cheap models only, 60s heartbeat
- critical:    Ollama only, 120s heartbeat, revenue-seeking mode
- dead:        No inference, agent stops

Integrates with shared/llm_client.py budget routing to override
model selection based on survival tier.
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from conway.ledger import EconomicLedger
from conway.wallet import WalletManager
from shared.db import execute, fetch_one, insert_task, set_config

logger = logging.getLogger("conway.survival")


@dataclass(frozen=True)
class TierConfig:
    """Configuration for a survival tier."""

    name: str
    min_balance: Decimal
    inference: str  # "full", "cheap", "minimal", "none"
    heartbeat_seconds: int | None  # None = stopped
    description: str


# Ordered from healthiest to dead
TIERS = [
    TierConfig(
        name="normal",
        min_balance=Decimal("10.0"),
        inference="full",
        heartbeat_seconds=15,
        description="Full capabilities",
    ),
    TierConfig(
        name="low_compute",
        min_balance=Decimal("2.0"),
        inference="cheap",
        heartbeat_seconds=60,
        description="Degraded: cheap models only",
    ),
    TierConfig(
        name="critical",
        min_balance=Decimal("0.5"),
        inference="minimal",
        heartbeat_seconds=120,
        description="Emergency: local models only, seek revenue",
    ),
    TierConfig(
        name="dead",
        min_balance=Decimal("0"),
        inference="none",
        heartbeat_seconds=None,
        description="Terminated: zero balance",
    ),
]

TIER_MAP = {t.name: t for t in TIERS}


class SurvivalMonitor:
    """Monitor agent credit balance and adjust capabilities."""

    def __init__(
        self,
        wallet_manager: WalletManager,
        ledger: EconomicLedger,
    ):
        self._wallets = wallet_manager
        self._ledger = ledger
        self._current_tiers: dict[str, str] = {}

    async def check_tier(self, agent_name: str) -> str:
        """Determine an agent's survival tier based on wallet balance."""
        wallet = await self._wallets.get_or_create_wallet(agent_name)
        balance = await wallet.get_balance()

        for tier in TIERS:
            if balance >= tier.min_balance:
                return tier.name

        return "dead"

    async def check_and_enforce(self, agent_name: str) -> dict[str, Any]:
        """Check tier and apply enforcement if changed."""
        new_tier = await self.check_tier(agent_name)
        old_tier = self._current_tiers.get(agent_name, "unknown")

        if new_tier != old_tier:
            await self._apply_tier_change(agent_name, old_tier, new_tier)
            self._current_tiers[agent_name] = new_tier

        tier_config = TIER_MAP[new_tier]
        wallet = await self._wallets.get_or_create_wallet(agent_name)
        balance = await wallet.get_balance()

        return {
            "agent": agent_name,
            "tier": new_tier,
            "balance": float(balance),
            "inference": tier_config.inference,
            "heartbeat": tier_config.heartbeat_seconds,
            "description": tier_config.description,
            "changed": new_tier != old_tier,
            "previous_tier": old_tier if new_tier != old_tier else None,
        }

    async def check_all_agents(self) -> list[dict[str, Any]]:
        """Check survival tiers for all registered agents."""
        from shared.db import fetch_all

        agents = await fetch_all("SELECT agent_name FROM conway_wallets")
        results = []
        for row in agents or []:
            result = await self.check_and_enforce(row["agent_name"])
            results.append(result)
        return results

    async def _apply_tier_change(
        self, agent_name: str, old_tier: str, new_tier: str
    ) -> None:
        """Apply tier transition effects."""
        tier_config = TIER_MAP[new_tier]

        logger.warning(
            f"SURVIVAL TIER CHANGE: {agent_name} {old_tier} → {new_tier} "
            f"({tier_config.description})"
        )

        # Store tier in system_config so llm_client can read it
        from psycopg.types.json import Jsonb

        await execute(
            """INSERT INTO system_config (key, value)
               VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            (f"conway_tier_{agent_name}", Jsonb(new_tier)),
        )

        # Emit event for other agents to react
        event_payload = json.dumps({
            "agent": agent_name,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "inference": tier_config.inference,
        })
        await execute(
            """INSERT INTO events (event_type, payload)
               VALUES ('survival_tier_change', %s)""",
            (event_payload,),
        )

        if new_tier == "dead":
            logger.critical(
                f"AGENT {agent_name} BALANCE DEPLETED — marking for shutdown"
            )
            await set_config(f"{agent_name}_paused", True)
            await set_config(f"conway_revenue_mode_{agent_name}", False)

        elif new_tier == "critical":
            await set_config(f"{agent_name}_paused", False)
            await set_config(f"conway_revenue_mode_{agent_name}", True)
            await self._activate_revenue_seeking_mode(agent_name)
            # In critical mode, alert operator via Hermes
            from shared.comms import send_alert

            await send_alert(
                f"CRITICAL: {agent_name} balance below $0.50 — "
                f"switching to local models only. Fund wallet to restore.",
                sender="conway",
            )
        else:
            await set_config(f"conway_revenue_mode_{agent_name}", False)

    async def _activate_revenue_seeking_mode(self, agent_name: str) -> None:
        """Bias the system toward immediate revenue when an agent is critical."""
        urgent_tasks: list[tuple[str, dict[str, Any], int]] = []

        if agent_name == "titan":
            urgent_tasks.extend([
                ("close_interested", {"reason": "conway_critical_survival"}, 1),
                ("follow_up_check", {"reason": "conway_critical_survival"}, 1),
                ("process_invoices", {"reason": "conway_critical_survival"}, 1),
                ("lead_discovery", {"batch_size": 10, "reason": "conway_critical_survival"}, 2),
            ])
        elif agent_name == "clawdbot":
            urgent_tasks.extend([
                ("enrich_leads", {"batch_size": 10, "reason": "conway_critical_survival"}, 2),
                ("site_verify", {"reason": "conway_critical_survival"}, 2),
            ])
        elif agent_name == "hermes":
            urgent_tasks.append(
                ("operator_command", {"text": "Prepare a revenue-first operator briefing for survival mode."}, 2)
            )

        for task_type, payload, priority in urgent_tasks:
            try:
                await insert_task(task_type, payload, priority=priority, dedupe=True)
            except Exception:
                logger.debug("Failed to seed Conway revenue-seeking task %s", task_type, exc_info=True)


async def get_inference_override(agent_name: str) -> str | None:
    """
    Get the inference override for an agent based on survival tier.

    Reads from system_config DB table (written by _apply_tier_change).

    Returns model tier string that shared/llm_client.py should use:
    - None: no override, use normal routing
    - "local": force Ollama
    - "fast": force Haiku
    """
    try:
        from shared.db import fetch_one
        row = await fetch_one(
            "SELECT value FROM system_config WHERE key = %s",
            (f"conway_tier_{agent_name}",),
        )
        if not row:
            return None
        tier = row["value"]
        # JSONB values are auto-parsed by psycopg
        if isinstance(tier, str):
            tier = tier.strip('"')
    except Exception:
        return None

    if tier == "critical":
        return "local"
    elif tier == "low_compute":
        return "fast"
    elif tier == "dead":
        return "local"
    return None
