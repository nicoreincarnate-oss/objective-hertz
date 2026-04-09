"""Perseus Scheduler — the strategic brain running inside OpenJarvis.

Replaces Perseus daemon's tick loop. Runs every ``tick_interval`` seconds,
assesses pipeline state across all vassals, decides priorities, enforces
budget, monitors health, and dispatches work.

This is what makes OpenJarvis the emperor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from openjarvis.core.events import EventBus, EventType
from openjarvis.vassals.priorities import decide_priorities

logger = logging.getLogger(__name__)


@dataclass
class PerseusConfig:
    """Configuration for the Perseus scheduler."""

    tick_interval: int = 60
    budget_monthly_cap: float = 800.0
    budget_alert_threshold: float = 0.8
    budget_hard_cap_action: str = "pause_titan"
    review_mode_first_n: int = 10
    health_check_interval: int = 300  # 5 minutes
    briefing_hour: int = 8  # 8 AM
    conway_check_every_n_ticks: int = 10  # Check Conway tiers every ~10 min
    stuck_detection_window: int = 3  # Ticks before declaring stuck


class PerseusScheduler:
    """The strategic brain. Replaces Perseus daemon.

    Runs as an OpenJarvis operator (async loop). Every tick:
    1. Assesses pipeline state across ALL vassals
    2. Runs deterministic priority logic (no LLM call)
    3. Enforces budget caps
    4. Monitors vassal health
    5. Dispatches prioritized work
    6. Records every decision for auditability
    """

    def __init__(
        self,
        bus: EventBus,
        vassal_discovery: Any,  # VassalDiscovery
        config: PerseusConfig | None = None,
        decision_audit: Any | None = None,  # DecisionAudit
        budget_guard: Any | None = None,  # BudgetGuard
        wakeup_queue: Any | None = None,  # WakeupQueue instance (Phase 16)
    ) -> None:
        self._bus = bus
        self._vassals = vassal_discovery
        self._config = config or PerseusConfig()
        self._decision_audit = decision_audit
        self._budget_guard = budget_guard
        self._wakeup_queue = wakeup_queue
        self._running = False
        self._last_health_check = 0.0
        self._tick_count = 0
        self._prev_states: list[dict[str, Any]] = []

    # ── Main loop ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the Perseus scheduler loop."""
        logger.info("=" * 60)
        logger.info("PERSEUS SCHEDULER — Strategic Brain Starting")
        logger.info("=" * 60)
        logger.info("Tick interval: %ds", self._config.tick_interval)
        logger.info("Budget cap: $%.0f/month", self._config.budget_monthly_cap)

        self._running = True

        # Start WakeupQueue if provided and enabled
        if self._wakeup_queue is not None:
            try:
                await self._wakeup_queue.start()
                logger.info("WakeupQueue active -- event-driven wakeup enabled")
            except Exception as exc:
                logger.error("WakeupQueue failed to start: %s -- falling back to polling", exc)
                self._wakeup_queue = None

        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.error("Perseus tick error: %s", exc, exc_info=True)

            # Event-driven wait OR fixed-interval sleep
            if self._wakeup_queue is not None and self._wakeup_queue._running:
                wakeup = await self._wakeup_queue.wait_for_wakeup(
                    "perseus", timeout=float(self._config.tick_interval)
                )
                if wakeup["woken_by"] == "event":
                    logger.info(
                        "Event-driven wakeup: reasons=%s",
                        wakeup["reasons"][:5],  # Cap log length
                    )
            else:
                await asyncio.sleep(self._config.tick_interval)

    async def stop(self) -> None:
        """Stop the scheduler."""
        logger.info("Perseus scheduler stopping...")
        self._running = False
        if self._wakeup_queue is not None:
            await self._wakeup_queue.shutdown()
            logger.info("WakeupQueue shut down")

    # ── Tick ──────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        """One strategic tick."""
        self._tick_count += 1
        now = time.time()

        # 0. Dispatch pending wakeup requests (if queue active)
        wakeup_dispatched: list[dict[str, Any]] = []
        if self._wakeup_queue is not None and self._wakeup_queue._running:
            try:
                wakeup_dispatched = await self._wakeup_queue.dispatch_pending("perseus")
                if wakeup_dispatched:
                    logger.debug(
                        "Dispatched %d wakeup requests: %s",
                        len(wakeup_dispatched),
                        [r.get("event_type", r.get("source")) for r in wakeup_dispatched[:5]],
                    )
            except Exception as exc:
                logger.warning("Wakeup dispatch failed (non-critical): %s", exc)

        # 1. Assess state across ALL vassals
        state = await self._assess_state()

        # 2. Deterministic priority logic
        priorities = decide_priorities(state)

        # 2b. Stuck pipeline detection — LLM override if stuck
        self._prev_states.append(state)
        if len(self._prev_states) > 5:
            self._prev_states = self._prev_states[-5:]
        if self._is_stuck():
            logger.warning("Pipeline appears stuck — asking LLM for new strategy")
            override = await self._llm_strategic_override(state, priorities)
            if override:
                priorities = override

        # 3. Budget enforcement
        budget_action = await self._enforce_budget(state)
        if budget_action:
            priorities["budget_action"] = budget_action

        # 4. Health check (every health_check_interval)
        if now - self._last_health_check >= self._config.health_check_interval:
            self._last_health_check = now
            await self._health_check()

        # 5. Dispatch prioritized work to Titan
        await self._dispatch(priorities)

        # 6. Alert on high-priority events
        if priorities.get("alert"):
            await self._send_alert(priorities["reasoning"])

        # 6b. Conway survival check (every N ticks)
        if self._tick_count % self._config.conway_check_every_n_ticks == 0:
            await self._conway_survival_check()

        # 7. Record decision
        await self._record_decision(state, priorities)

        # 8. Publish tick event
        tick_event = {
            "sub_type": "perseus_tick",
            "tick": self._tick_count,
            "scheduled": len(priorities.get("schedule", [])),
            "skipped": len(priorities.get("skip", [])),
            "reasoning": priorities.get("reasoning", ""),
        }
        # Include wakeup stats if queue active
        if self._wakeup_queue is not None:
            tick_event["wakeup_stats"] = self._wakeup_queue.stats()
            tick_event["wakeup_dispatched"] = len(wakeup_dispatched)
        self._bus.publish(EventType.CUSTOM, tick_event)

        # 9. Wakeup queue maintenance (every 5 ticks ~ 5 minutes)
        if self._wakeup_queue is not None and self._tick_count % 5 == 0:
            try:
                expired = await self._wakeup_queue.expire_stale_requests()
                if expired:
                    logger.debug("Expired %d stale wakeup requests", expired)
            except Exception as exc:
                logger.debug("Wakeup expiry failed (non-critical): %s", exc)

        # 10. Wakeup queue daily cleanup (every ~24h = 1440 ticks at 60s)
        if self._wakeup_queue is not None and self._tick_count % 1440 == 0:
            try:
                cleaned = await self._wakeup_queue.cleanup_old_requests(older_than_hours=24)
                if cleaned:
                    logger.info("Cleaned up %d old wakeup requests", cleaned)
            except Exception as exc:
                logger.debug("Wakeup cleanup failed (non-critical): %s", exc)

        if self._tick_count % 10 == 0:  # Log summary every 10 ticks
            logger.info(
                "Tick %d: scheduled=%s reasoning=%s",
                self._tick_count,
                priorities.get("schedule", []),
                priorities.get("reasoning", "")[:100],
            )

    # ── State assessment ──────────────────────────────────────────────

    async def _assess_state(self) -> dict[str, Any]:
        """Assess pipeline state by querying vassals."""
        state: dict[str, Any] = {}

        # Query Titan for pipeline status
        titan = self._vassals.get("titan")
        if titan and titan.healthy:
            try:
                raw = self._vassals.call("titan", "pipeline_status")
                state = json.loads(raw) if isinstance(raw, str) else raw
            except Exception as exc:
                logger.warning("Failed to assess Titan pipeline: %s", exc)
                state = {}

        # Query budget
        try:
            budget_raw = self._vassals.call("titan", "budget_status")
            budget = json.loads(budget_raw) if isinstance(budget_raw, str) else budget_raw
            state["budget"] = budget
        except Exception:
            state["budget"] = {}

        # Query other vassals for health context
        for name in ["hermes", "clawdbot"]:
            vassal = self._vassals.get(name)
            if vassal and vassal.healthy:
                state[f"{name}_healthy"] = True
            else:
                state[f"{name}_healthy"] = False

        return state

    # ── Budget enforcement ────────────────────────────────────────────

    async def _enforce_budget(self, state: dict[str, Any]) -> str | None:
        """Check budget and take action if needed."""
        budget = state.get("budget", {})
        percent = budget.get("percent_used", 0)

        if not percent and self._budget_guard:
            try:
                percent = self._budget_guard.percent_used()
            except Exception:
                return None

        if percent >= 100:
            logger.warning("BUDGET EXCEEDED (%.0f%%) — pausing pipeline work", percent)
            self._bus.publish(EventType.CUSTOM, {
                "sub_type": "budget_exceeded",
                "percent": percent,
                "action": self._config.budget_hard_cap_action,
            })
            # Tell Titan to pause
            try:
                self._vassals.call("titan", "config_set", key="titan_paused", value=True)
            except Exception:
                pass
            return "paused_pipeline"

        elif percent >= self._config.budget_alert_threshold * 100:
            logger.info("Budget warning: %.0f%% used — downgrading to local models", percent)
            self._bus.publish(EventType.CUSTOM, {
                "sub_type": "budget_warning",
                "percent": percent,
            })
            # Tell Titan to use local models only
            try:
                self._vassals.call("titan", "config_set", key="model_tier", value="local_only")
            except Exception:
                pass
            return "downgraded_to_local"

        else:
            # Budget OK — ensure Titan is unpaused (unless manually paused)
            try:
                self._vassals.call("titan", "config_set", key="titan_paused", value=False)
            except Exception:
                pass
            return None

    # ── Health check ──────────────────────────────────────────────────

    async def _health_check(self) -> None:
        """Check health of all vassals."""
        health = self._vassals.health_check_all()
        for name, status in health.items():
            if not status.get("healthy"):
                logger.warning("Vassal %s is unhealthy: %s", name, status.get("error", "unknown"))
                self._bus.publish(EventType.CUSTOM, {
                    "sub_type": "vassal_unhealthy",
                    "vassal": name,
                    "error": status.get("error", ""),
                })
                # Try to alert via Hermes
                await self._send_alert(f"Vassal '{name}' is down: {status.get('error', 'unknown')}")

        # Try to rediscover unhealthy vassals
        recovered = self._vassals.rediscover_unhealthy()
        for name in recovered:
            logger.info("Vassal %s recovered", name)
            await self._send_alert(f"Vassal '{name}' recovered and reconnected")

    # ── Dispatch ──────────────────────────────────────────────────────

    async def _dispatch(self, priorities: dict[str, Any]) -> None:
        """Dispatch prioritized tasks to Titan."""
        for task_name in priorities.get("schedule", []):
            try:
                self._vassals.call(
                    "titan", "task_dispatch",
                    task_type=task_name,
                    payload={"priority_reason": priorities.get("reasoning", "")},
                    priority=5,
                )
            except Exception as exc:
                logger.warning("Failed to dispatch %s to Titan: %s", task_name, exc)

    # ── Alerts ────────────────────────────────────────────────────────

    async def _send_alert(self, message: str) -> None:
        """Send alert via Hermes (if available)."""
        hermes = self._vassals.get("hermes")
        if hermes and hermes.healthy:
            try:
                self._vassals.call("hermes", "alert_urgent", text=message)
            except Exception:
                pass
        # Always publish to EventBus regardless
        self._bus.publish(EventType.CUSTOM, {
            "sub_type": "perseus_alert",
            "message": message,
        })

    # ── Decision audit ────────────────────────────────────────────────

    async def _record_decision(self, state: dict[str, Any], priorities: dict[str, Any]) -> None:
        """Record the scheduling decision for auditability."""
        if self._decision_audit:
            try:
                self._decision_audit.record(
                    agent="openjarvis.perseus_scheduler",
                    decision_type="priority_allocation",
                    context=state,
                    decision=priorities,
                    reasoning=priorities.get("reasoning", ""),
                )
            except Exception:
                pass

    # ── Stuck pipeline detection ─────────────────────────────────────

    def _is_stuck(self) -> bool:
        """Check if pipeline state hasn't changed meaningfully in N cycles."""
        window = self._config.stuck_detection_window
        if len(self._prev_states) < window:
            return False
        keys = ("hot_leads", "ready_to_close", "ready_to_deliver", "ready_to_invoice")
        recent = self._prev_states[-window:]
        first = {k: recent[0].get(k, 0) for k in keys}
        return all({k: s.get(k, 0) for k in keys} == first for s in recent[1:])

    async def _llm_strategic_override(self, state: dict, current: dict) -> dict | None:
        """Ask LLM for a new strategy when deterministic rules aren't working."""
        try:
            from shared.llm_client import llm

            response = await llm.generate(
                f"You are OpenJarvis, the boss. Your revenue pipeline is stuck.\n\n"
                f"Pipeline state: {json.dumps(state)}\n"
                f"Current plan: {json.dumps(current)}\n"
                f"This state hasn't changed in {self._config.stuck_detection_window}+ cycles. "
                f"What should we do differently?\n"
                f"Return JSON: {{\"schedule\": [\"task1\", \"task2\"], \"reasoning\": \"why\"}}",
                model="smart", max_tokens=400, temperature=0.3,
                operation="openjarvis._llm_strategic_override", daemon_name="openjarvis",
            )
            match = re.search(r'\{[^}]+\}', response)
            if match:
                override = json.loads(match.group())
                if "schedule" in override:
                    logger.info("LLM strategic override: %s", override.get("reasoning", "")[:100])
                    return override
        except Exception as exc:
            logger.warning("LLM strategic override failed: %s", exc)
        return None

    # ── Conway survival ───────────────────────────────────────────────

    async def _conway_survival_check(self) -> None:
        """Check agent wallet tiers via Conway economic system."""
        try:
            from shared.config import config
            if not config.conway.enabled:
                return

            from conway.ledger import EconomicLedger
            from conway.survival import SurvivalMonitor
            from conway.wallet import WalletManager

            wm = WalletManager()
            ledger = EconomicLedger()
            monitor = SurvivalMonitor(wm, ledger)
            results = await monitor.check_all_agents()

            for r in results:
                if r.get("changed"):
                    msg = (
                        f"Conway tier change: {r['agent']} -> {r['tier']} "
                        f"(balance: ${r['balance']:.2f})"
                    )
                    logger.warning(msg)
                    await self._send_alert(msg)

        except Exception as exc:
            logger.debug("Conway survival check: %s", exc)

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return scheduler status for CLI/dashboard."""
        base = {
            "running": self._running,
            "tick_count": self._tick_count,
            "tick_interval": self._config.tick_interval,
            "budget_cap": self._config.budget_monthly_cap,
            "vassals": self._vassals.summary(),
        }
        if self._wakeup_queue is not None:
            base["wakeup_queue"] = self._wakeup_queue.stats()
        return base


__all__ = ["PerseusConfig", "PerseusScheduler"]
