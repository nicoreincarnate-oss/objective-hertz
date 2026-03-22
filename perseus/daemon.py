"""
Perseus Master Daemon — The brain that coordinates everything.
Runs 24/7. Reads pipeline state, decides priorities, monitors health, enforces budget.
Not a cron scheduler — an orchestrator that thinks.
"""

import asyncio
import json
import signal
import time
import logging

from shared.config import config
from shared.logging_config import setup_logging
from shared import db
from shared.agent_base import AgentBase

from perseus.scheduler import SCHEDULES, SCHEDULE_MAP
from perseus.agent_registry import heartbeat, check_agent_health

logger = setup_logging("perseus")

# How often Perseus re-evaluates priorities (seconds).
STRATEGIC_TICK_INTERVAL = 60


async def _handle_operator_message(payload: dict):
    """Acknowledge an operator note sent to the master orchestrator."""
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ValueError("operator message is required")

    await db.emit_event(
        "agent_message_ack",
        {
            "agent": "perseus",
            "reply": "Perseus logged your directive and will factor it into the next strategic tick.",
            "operator_message": message,
            "priority": payload.get("priority", "priority"),
            "source": payload.get("source", "war_room"),
        },
    )
    logger.info("Perseus received operator message: %s", message)


TASK_HANDLERS = {
    "perseus_operator_message": _handle_operator_message,
}


class PerseusDaemon(AgentBase):
    name = "perseus"
    description = "Master AI orchestrator — reads pipeline state, decides priorities, self-heals."

    def __init__(self):
        super().__init__()
        self._running = False
        self._last_run: dict[str, float] = {}
        self._last_strategic_tick: float = 0.0

    async def start(self):
        """Start Perseus master loop."""
        logger.info("=" * 60)
        logger.info("PERSEUS — Master AI System Starting")
        logger.info("=" * 60)

        await db.init_pool()
        await self.register()
        self._stopped.clear()
        self._running = True

        now = time.time()
        for sched in SCHEDULES:
            self._last_run[sched.name] = now

        logger.info(f"Loaded {len(SCHEDULES)} schedules")
        logger.info("Perseus is LIVE.")

        try:
            while self._running:
                self.begin_work("loop:tick")
                try:
                    await self._tick()
                except Exception as e:
                    logger.error(f"Perseus tick error: {e}", exc_info=True)
                finally:
                    self.finish_work("loop:tick")
                await asyncio.sleep(10)
        finally:
            await self.finalize_shutdown()

    async def stop(self):
        """Gracefully stop Perseus."""
        logger.info("Perseus shutdown requested...")
        self.request_shutdown()
        await self.wait_for_work_drain()
        await self.wait_until_stopped()
        logger.info("Perseus stopped.")

    async def health_check(self) -> dict:
        agents = await check_agent_health()
        return {
            "agent": self.name,
            "status": "running" if self._running else "stopped",
            "managed_agents": agents,
        }

    # ── Main tick ────────────────────────────────────────────────

    async def _tick(self):
        """One tick: always run non-skippable schedules, strategically run the rest."""
        now = time.time()
        await heartbeat(self.name)
        await self._process_task_queue()

        # 1. Always run non-skippable schedules on their intervals (health, budget, etc.)
        for sched in SCHEDULES:
            if sched.skippable:
                continue
            elapsed = now - self._last_run.get(sched.name, 0)
            if elapsed >= sched.interval_seconds:
                self._last_run[sched.name] = now
                task_id = await db.insert_task(sched.name, {"scheduled": True})
                if task_id:
                    logger.debug(f"Scheduled (fixed): {sched.name}")

        # 2. Strategic tick: assess pipeline and decide what to prioritize
        if now - self._last_strategic_tick >= STRATEGIC_TICK_INTERVAL:
            self._last_strategic_tick = now
            await self._strategic_tick(now)

        # 3. Budget enforcement
        await self._enforce_budget()

        # 4. Infrastructure health
        await self._check_infrastructure()

        # 5. Prune old tasks
        await db.execute(
            """DELETE FROM task_queue
               WHERE status IN ('completed', 'failed')
               AND completed_at < NOW() - INTERVAL '24 hours'"""
        )

    async def _process_task_queue(self):
        """Process operator messages addressed to Perseus."""
        tasks = await self.get_pending_tasks()
        for task in tasks:
            if self._shutdown_requested:
                break
            task_type = task["task_type"]
            handler = TASK_HANDLERS.get(task_type)
            if not handler:
                continue

            claimed = await self.claim_task(task["id"])
            if not claimed:
                continue

            work_id = f"task:{task['id']}"
            self.begin_work(work_id)
            try:
                payload = task.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                await handler(payload)
                await self.complete_task(task["id"])
                logger.debug("Task %s (%s) completed", task["id"], task_type)
            except Exception as e:
                await self.fail_task(task["id"], str(e))
                logger.error("Task %s (%s) failed: %s", task["id"], task_type, e)
            finally:
                self.finish_work(work_id)

    # ── Strategic allocation ─────────────────────────────────────

    async def _strategic_tick(self, now: float):
        """Assess pipeline state and decide which skippable tasks to run."""
        state = await _assess_pipeline_state()

        # Decide priorities using rules: no LLM call needed for basic prioritization.
        # This is deterministic logic based on pipeline counts — fast and free.
        priorities = _decide_priorities(state)

        # Record the decision for auditability
        try:
            await db.execute(
                """INSERT INTO agent_decisions (agent, decision_type, context, decision, reasoning)
                   VALUES (%s, 'priority_allocation', %s, %s, %s)""",
                (
                    self.name,
                    json.dumps(state),
                    json.dumps(priorities),
                    priorities.get("reasoning", ""),
                ),
            )
        except Exception:
            pass  # Decision logging is non-critical

        # Schedule the prioritized tasks
        for task_name in priorities.get("schedule", []):
            sched = SCHEDULE_MAP.get(task_name)
            if not sched:
                continue
            elapsed = now - self._last_run.get(task_name, 0)
            # Respect minimum interval: at least 50% of the configured interval
            min_interval = sched.interval_seconds * 0.5
            if elapsed < min_interval:
                continue
            self._last_run[task_name] = now
            task_id = await db.insert_task(task_name, {"scheduled": True, "priority_reason": priorities.get("reasoning", "")})
            if task_id:
                logger.info(f"Scheduled (strategic): {task_name} — {priorities.get('reasoning', '')[:80]}")

        # Log what was skipped
        skipped = priorities.get("skip", [])
        if skipped:
            logger.debug(f"Skipped this cycle: {', '.join(skipped)}")

    # ── Budget enforcement ───────────────────────────────────────

    async def _enforce_budget(self):
        """Check budget and enforce cap."""
        try:
            from tools.budget_guard import BudgetGuard
            guard = BudgetGuard()
            status = await guard.check_budget()
            percent = status.get("percent_used", 0)

            if status.get("exceeded"):
                already_paused = await db.get_config("titan_paused", False)
                if not already_paused:
                    await db.set_config("titan_paused", True)
                    await db.set_config("clawdbot_paused", True)
                    logger.warning("BUDGET EXCEEDED — Titan and ClawdBot PAUSED")
                    await self.emit_event("budget_exceeded", {
                        **status,
                        "action": "paused_titan_and_clawdbot",
                    })

            elif percent >= config.budget.alert_threshold * 100:
                await self.emit_event("budget_warning", {
                    **status,
                    "action": "llm_downgraded_to_ollama",
                })

            else:
                was_paused = await db.get_config("titan_paused", False)
                if was_paused:
                    manual_pause = await db.get_config("titan_manual_pause", False)
                    if not manual_pause:
                        await db.set_config("titan_paused", False)
                        await db.set_config("clawdbot_paused", False)
                        logger.info("Budget OK — Titan and ClawdBot RESUMED")
                        await self.emit_event("budget_ok", {
                            **status,
                            "action": "resumed_titan_and_clawdbot",
                        })

        except (ImportError, Exception) as e:
            logger.debug(f"Budget enforcement skipped: {e}")

    # ── Infrastructure health ────────────────────────────────────

    async def _check_infrastructure(self):
        """Check infrastructure dependencies and store status for other agents."""
        try:
            from perseus.health import check_infrastructure
            health = await check_infrastructure()
            await db.set_config("infra_health", health)
        except (ImportError, Exception) as e:
            logger.debug(f"Infrastructure health check skipped: {e}")


# ── Pipeline state assessment (module-level, no self needed) ─────

async def _assess_pipeline_state() -> dict:
    """Build a snapshot of the full pipeline for strategic decision-making."""
    try:
        rows = await db.fetch_all(
            "SELECT status, COUNT(*) as count FROM clients GROUP BY status"
        )
        stage_counts = {r["status"]: r["count"] for r in rows}
    except Exception:
        stage_counts = {}

    try:
        pending_tasks = await db.fetch_all(
            """SELECT task_type, COUNT(*) as count FROM task_queue
               WHERE status IN ('pending', 'running')
               GROUP BY task_type"""
        )
        task_backlog = {r["task_type"]: r["count"] for r in pending_tasks}
    except Exception:
        task_backlog = {}

    try:
        recent_errors = await db.fetch_val(
            """SELECT COUNT(*) FROM events
               WHERE event_type = 'pipeline_error'
               AND created_at > NOW() - INTERVAL '1 hour'"""
        ) or 0
    except Exception:
        recent_errors = 0

    try:
        pending_reviews = await db.fetch_val(
            "SELECT COUNT(*) FROM review_queue WHERE status = 'pending_review'"
        ) or 0
    except Exception:
        pending_reviews = 0

    return {
        "stage_counts": stage_counts,
        "task_backlog": task_backlog,
        "recent_errors": recent_errors,
        "pending_reviews": pending_reviews,
        "hot_leads": stage_counts.get("interested", 0) + stage_counts.get("demo_built", 0),
        "ready_to_close": stage_counts.get("proposal_sent", 0) + stage_counts.get("negotiating", 0),
        "ready_to_deliver": stage_counts.get("closed", 0),
        "ready_to_invoice": stage_counts.get("deployed", 0),
        "top_of_funnel": stage_counts.get("discovered", 0) + stage_counts.get("researched", 0),
        "outreach_active": stage_counts.get("email_drafted", 0) + stage_counts.get("email_sent", 0),
    }


def _decide_priorities(state: dict) -> dict:
    """
    Deterministic priority logic — no LLM call, just pipeline math.

    Rule: always prioritize revenue-closest work first.
    Money on the table > leads in the pipe > new discovery.
    """
    schedule_now: list[str] = []
    skip_now: list[str] = []
    reasons: list[str] = []

    hot = state.get("hot_leads", 0)
    ready_close = state.get("ready_to_close", 0)
    ready_deliver = state.get("ready_to_deliver", 0)
    ready_invoice = state.get("ready_to_invoice", 0)
    top_funnel = state.get("top_of_funnel", 0)
    outreach = state.get("outreach_active", 0)

    # Priority 1: Invoices waiting → money sitting on the table
    if ready_invoice > 0:
        schedule_now.append("process_invoices")
        reasons.append(f"{ready_invoice} leads ready to invoice")

    # Priority 2: Sites to build → unblock invoicing
    if ready_deliver > 0:
        schedule_now.append("build_sites")
        schedule_now.append("site_verify")
        reasons.append(f"{ready_deliver} sites to build")

    # Priority 3: Hot leads → close them before they cool off
    if hot > 0 or ready_close > 0:
        schedule_now.append("close_interested")
        schedule_now.append("follow_up_check")
        reasons.append(f"{hot} hot leads, {ready_close} ready to close")

    # Priority 4: Active outreach needs analytics + follow-ups
    if outreach > 0:
        schedule_now.append("sync_analytics")
        schedule_now.append("email_send")
        schedule_now.append("email_compose")
        schedule_now.append("follow_up_check")

    # Priority 5: Top of funnel — only if we're not overwhelmed downstream
    if hot <= 3 and ready_deliver <= 2:
        schedule_now.append("lead_discovery")
        schedule_now.append("lead_research")
        schedule_now.append("enrich_leads")
    else:
        skip_now.extend(["lead_discovery", "lead_research", "enrich_leads"])
        reasons.append(f"Skipping discovery: {hot} hot leads + {ready_deliver} to deliver — focus downstream")

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for name in schedule_now:
        if name not in seen:
            seen.add(name)
            deduped.append(name)

    # Everything skippable that wasn't scheduled
    all_skippable = {s.name for s in SCHEDULES if s.skippable}
    implicit_skip = all_skippable - seen
    skip_now.extend(implicit_skip - set(skip_now))

    reasoning = "; ".join(reasons) if reasons else "Normal pipeline flow"

    return {
        "schedule": deduped,
        "skip": skip_now,
        "reasoning": reasoning,
    }


async def main():
    """Entry point for Perseus daemon."""
    perseus = PerseusDaemon()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(perseus.stop()))

    await perseus.start()


if __name__ == "__main__":
    asyncio.run(main())
