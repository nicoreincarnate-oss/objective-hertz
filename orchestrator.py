"""OpenJarvis Orchestrator — THE system brain. Replaces Perseus entirely.

Responsibilities:
- Boots the OJ runtime (EventBus, TraceStore, AgentManager, AuditLogger)
- Runs the revenue pipeline via WorkflowEngine on a schedule
- Strategic priority allocation (absorbed from Perseus)
- Monitors agent health via A2A endpoints
- Enforces budget (fiat + Conway crypto)
- Monitors Conway survival tiers
- Does NOT execute pipeline stages (Titan owns execution)

Usage:
    python orchestrator.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time

logger = logging.getLogger("openjarvis.orchestrator")


class Orchestrator:
    """The sole orchestrator — boss of Titan, Hermes, and ClawdBot."""

    def __init__(self):
        self._running = False
        self._pipeline_interval = int(os.environ.get("PIPELINE_INTERVAL", "300"))  # 5 min
        self._health_interval = int(os.environ.get("HEALTH_INTERVAL", "60"))  # 1 min
        self._budget_interval = int(os.environ.get("BUDGET_INTERVAL", "120"))  # 2 min
        self._strategic_interval = 60  # 60s strategic tick (from Perseus)
        self._last_run: dict[str, float] = {}

    async def start(self):
        """Start the orchestrator."""
        self._running = True
        logger.info("OpenJarvis Orchestrator starting — I am the boss now.")

        # Boot OJ runtime
        from shared.oj_bridge import (
            get_bus,
            get_trace_store,
            get_agent_manager,
            get_audit_logger,
        )
        get_bus()
        get_trace_store()
        get_agent_manager()
        get_audit_logger()

        # Initialize DB
        from shared.db import init_pool
        await init_pool()

        logger.info("OJ runtime initialized")

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, self._shutdown)

        # Run concurrent loops — the boss runs everything
        loops = [
            self._pipeline_loop(),
            self._health_loop(),
            self._budget_loop(),
            self._strategic_loop(),
            self._command_loop(),      # Listen for operator commands
            self._followup_loop(),     # Monitor agent progress
        ]

        # Add Conway survival monitoring if enabled
        from shared.config import config
        if config.conway.enabled:
            loops.append(self._conway_survival_loop())

        await asyncio.gather(*loops)

    def _shutdown(self):
        logger.info("Shutdown signal received")
        self._running = False

    async def _pipeline_loop(self):
        """Run the revenue pipeline on interval."""
        while self._running:
            # Check for agent recommendations
            try:
                from shared.comms import get_pending_recommendations
                recs = await get_pending_recommendations("perseus", since_minutes=30, limit=5)
                for rec in recs:
                    payload = rec.get("payload", {})
                    if isinstance(payload, str):
                        import json
                        payload = json.loads(payload)
                    logger.info(f"Recommendation from {payload.get('from', '?')}: {payload.get('topic', '')}")
            except Exception:
                pass

            try:
                from titan.workflow_pipeline import run_pipeline
                logger.info("Starting pipeline cycle...")
                result = run_pipeline()
                if not result.success:
                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_warning", {
                        "text": f"Pipeline cycle had failures: {[s.node_id for s in result.steps if not s.success]}",
                    })
            except Exception as exc:
                logger.error("Pipeline cycle error: %s", exc, exc_info=True)
                try:
                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_urgent", {
                        "text": f"Pipeline cycle crashed: {exc}",
                    })
                except Exception:
                    pass

            await asyncio.sleep(self._pipeline_interval)

    async def _health_loop(self):
        """Check agent health via A2A endpoints."""
        while self._running:
            try:
                from shared.oj_bridge import call_agent, AGENT_URLS

                statuses = {}
                for agent_name in AGENT_URLS:
                    result = call_agent(agent_name, "health_check")
                    status = result.get("status", "unknown")
                    statuses[agent_name] = status
                    if status not in ("running", "ok"):
                        logger.warning("Agent %s health: %s", agent_name, status)

                # Publish health to EventBus
                from shared.oj_bridge import get_bus
                from openjarvis.core.events import EventType
                get_bus().publish(EventType.A2A_REMOTE_EVENT, {
                    "event_type": "health_report",
                    "agents": statuses,
                })

            except Exception as exc:
                logger.error("Health check error: %s", exc)

            await asyncio.sleep(self._health_interval)

    async def _budget_loop(self):
        """Enforce budget limits."""
        while self._running:
            try:
                from tools.budget_guard import BudgetGuard
                guard = BudgetGuard()
                budget = await guard.check_budget()

                if budget.get("exceeded"):
                    logger.warning("Budget exceeded — pausing agents")
                    from shared import db
                    await db.set_config("titan_paused", True)
                    await db.set_config("clawdbot_paused", True)

                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_urgent", {
                        "text": f"Budget exceeded: ${budget.get('total_spent', 0):.0f}/${budget.get('cap', 800)}",
                    })

                elif budget.get("percent_used", 0) >= 80:
                    from shared.oj_bridge import call_agent
                    call_agent("hermes", "alert_warning", {
                        "text": f"Budget at {budget.get('percent_used', 0):.0f}%",
                    })

            except Exception as exc:
                logger.error("Budget check error: %s", exc)

            await asyncio.sleep(self._budget_interval)

    async def _strategic_loop(self):
        """Strategic priority allocation — the boss decides what the team works on.

        Deterministic rules as baseline, with LLM override when pipeline is stuck.
        """
        self._prev_states: list[dict] = []

        while self._running:
            try:
                from shared.db import execute as db_execute, insert_task
                from shared.pipeline import assess_pipeline_state

                state = await assess_pipeline_state()
                priorities = _decide_priorities(state)

                # Detect stuck pipeline: same state for 3+ cycles
                self._prev_states.append(state)
                if len(self._prev_states) > 5:
                    self._prev_states = self._prev_states[-5:]

                if self._is_stuck():
                    logger.warning("Pipeline appears stuck — asking LLM for new strategy")
                    llm_override = await self._llm_strategic_override(state, priorities)
                    if llm_override:
                        priorities = llm_override

                # Record decision
                try:
                    await db_execute(
                        """INSERT INTO agent_decisions (agent, decision_type, context, decision, reasoning)
                           VALUES (%s, 'priority_allocation', %s, %s, %s)""",
                        ("orchestrator", json.dumps(state),
                         json.dumps(priorities), priorities.get("reasoning", "")),
                    )
                except Exception:
                    pass

                # Schedule prioritized tasks
                now = time.time()
                for task_name in priorities.get("schedule", []):
                    elapsed = now - self._last_run.get(task_name, 0)
                    if elapsed < 30:
                        continue
                    self._last_run[task_name] = now
                    task_id = await insert_task(
                        task_name,
                        {"scheduled": True, "priority_reason": priorities.get("reasoning", "")},
                    )
                    if task_id:
                        logger.info(f"Scheduled: {task_name} — {priorities.get('reasoning', '')[:80]}")

            except Exception as exc:
                logger.debug(f"Strategic tick: {exc}")

            await asyncio.sleep(self._strategic_interval)

    def _is_stuck(self) -> bool:
        """Check if pipeline state hasn't changed meaningfully in 3+ cycles."""
        if len(self._prev_states) < 3:
            return False
        # Compare last 3 states — if key metrics are identical, we're stuck
        keys = ("hot_leads", "ready_to_close", "ready_to_deliver", "ready_to_invoice")
        recent = self._prev_states[-3:]
        first = {k: recent[0].get(k, 0) for k in keys}
        return all({k: s.get(k, 0) for k in keys} == first for s in recent[1:])

    async def _llm_strategic_override(self, state: dict, current: dict) -> dict | None:
        """Ask LLM for a new strategy when deterministic rules aren't working."""
        try:
            from shared.llm_client import llm
            from shared.comms import get_pending_recommendations

            recs = await get_pending_recommendations("orchestrator", since_minutes=60, limit=5)
            rec_text = "\n".join(
                f"- {r.get('payload', {}).get('topic', '?')}: {str(r.get('payload', {}).get('message', ''))[:100]}"
                for r in recs
            ) or "None"

            response = await llm.generate(
                f"You are OpenJarvis, the boss. Your revenue pipeline is stuck.\n\n"
                f"Pipeline state: {json.dumps(state)}\n"
                f"Current plan: {json.dumps(current)}\n"
                f"Agent recommendations:\n{rec_text}\n\n"
                f"This state hasn't changed in 3+ cycles. What should we do differently?\n"
                f"Return JSON: {{\"schedule\": [\"task1\", \"task2\"], \"reasoning\": \"why\"}}",
                tier="smart", max_tokens=400, temperature=0.3)

            # Parse LLM response
            import re
            match = re.search(r'\{[^}]+\}', response)
            if match:
                override = json.loads(match.group())
                if "schedule" in override:
                    logger.info(f"LLM strategic override: {override.get('reasoning', '')[:100]}")
                    return override
        except Exception as e:
            logger.debug(f"LLM strategic override failed: {e}")
        return None

    async def _conway_survival_loop(self):
        """Monitor agent survival tiers (Conway economic system)."""
        while self._running:
            try:
                from conway.wallet import WalletManager
                from conway.ledger import EconomicLedger
                from conway.survival import SurvivalMonitor

                wm = WalletManager()
                ledger = EconomicLedger()
                monitor = SurvivalMonitor(wm, ledger)

                results = await monitor.check_all_agents()
                for r in results:
                    if r.get("changed"):
                        logger.warning(
                            f"Conway tier change: {r['agent']} → {r['tier']} "
                            f"(balance: ${r['balance']:.2f})"
                        )
            except Exception as exc:
                logger.debug(f"Conway survival check: {exc}")

            await asyncio.sleep(120)  # Check every 2 min


    async def _command_loop(self):
        """Listen for operator commands and decompose into agent work.

        The boss receives high-level instructions and turns them into
        specific tasks for the team.
        """
        while self._running:
            try:
                from shared.db import fetch_all, execute as db_execute

                # Check for unprocessed operator commands
                commands = await fetch_all(
                    """SELECT id, payload FROM task_queue
                       WHERE task_type = 'operator_command'
                       AND status = 'pending'
                       ORDER BY created_at ASC LIMIT 3"""
                )
                for cmd in commands or []:
                    task_id = cmd["id"]
                    payload = cmd.get("payload", {})
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    text = payload.get("text", payload.get("description", ""))
                    if not text:
                        await db_execute(
                            "UPDATE task_queue SET status = 'completed' WHERE id = %s",
                            (task_id,))
                        continue

                    # Claim the command
                    await db_execute(
                        "UPDATE task_queue SET status = 'running' WHERE id = %s",
                        (task_id,))

                    # Boss decomposes command into agent tasks
                    await self._handle_command(text)

                    await db_execute(
                        "UPDATE task_queue SET status = 'completed' WHERE id = %s",
                        (task_id,))

            except Exception as exc:
                logger.debug(f"Command loop: {exc}")

            await asyncio.sleep(10)  # Check every 10s — commands are urgent

    async def _handle_command(self, command: str):
        """The boss decomposes a high-level command into agent tasks."""
        from shared.llm_client import llm
        from shared.comms import delegate_task
        from shared.db import execute as db_execute

        logger.info(f"Boss received command: {command[:100]}")

        try:
            plan = await llm.generate(
                f"You are OpenJarvis, the boss of a 3-agent business team:\n"
                f"- Titan: revenue pipeline (lead discovery, email outreach, deals, invoicing, payments)\n"
                f"- ClawdBot: skills executor (browser automation, web scraping, site building, research, image gen)\n"
                f"- Hermes: operator comms (Telegram alerts, dashboard, briefings)\n\n"
                f"The operator commands: \"{command}\"\n\n"
                f"Decompose this into specific, actionable tasks for your agents.\n"
                f"Available task_types for Titan: lead_discovery, lead_research, email_compose, email_send, "
                f"follow_up_check, close_interested, build_sites, process_invoices, sync_analytics\n"
                f"Available task_types for ClawdBot: skill_execute, web_scrape, browser_task, enrich_lead, "
                f"site_verify, image_generation, capability_resolve\n"
                f"Available task_types for Hermes: send_alert, morning_briefing\n\n"
                f"Return ONLY valid JSON:\n"
                f"{{\"tasks\": [{{\"agent\": \"titan|clawdbot|hermes\", \"task_type\": \"...\", "
                f"\"description\": \"...\", \"priority\": 1}}], "
                f"\"reasoning\": \"why this plan\"}}",
                tier="genius", max_tokens=800, temperature=0.3)

            # Parse response
            import re
            match = re.search(r'\{[\s\S]*\}', plan)
            if not match:
                logger.error(f"Boss couldn't parse plan from LLM: {plan[:200]}")
                return

            parsed = json.loads(match.group())
            tasks = parsed.get("tasks", [])
            reasoning = parsed.get("reasoning", "")

            logger.info(f"Boss plan: {len(tasks)} tasks — {reasoning[:100]}")

            # Dispatch tasks to agents
            for task in tasks:
                agent = task.get("agent", "")
                task_type = task.get("task_type", "")
                description = task.get("description", "")
                priority = task.get("priority", 3)

                if agent and task_type:
                    await delegate_task("orchestrator", agent, task_type,
                        {"description": description, "boss_command": command[:200]},
                        priority=priority)
                    logger.info(f"Boss → {agent}: {task_type} (p{priority}) — {description[:80]}")

            # Record the command execution
            await db_execute(
                """INSERT INTO agent_decisions (agent, decision_type, context, decision, reasoning)
                   VALUES (%s, 'boss_command', %s, %s, %s)""",
                ("orchestrator", json.dumps({"command": command}),
                 json.dumps({"tasks": tasks}),
                 f"Decomposed into {len(tasks)} tasks: {reasoning[:200]}"),
            )

        except Exception as e:
            logger.error(f"Boss command handling failed: {e}")
            # At minimum, alert Hermes that the command couldn't be processed
            from shared.comms import send_alert
            await send_alert(f"Boss couldn't process command: {command[:100]}. Error: {e}", sender="orchestrator")

    async def _followup_loop(self):
        """Boss monitors team progress and intervenes when needed."""
        while self._running:
            try:
                from shared.db import fetch_all, fetch_val
                from shared.comms import ask_agent, send_alert

                # Check for stale tasks (assigned but not completed in 10+ minutes)
                stale = await fetch_all(
                    """SELECT id, task_type, assigned_agent, created_at FROM task_queue
                       WHERE status = 'running'
                       AND created_at < NOW() - INTERVAL '10 minutes'
                       LIMIT 5"""
                )
                for task in stale or []:
                    agent = task.get("assigned_agent", "unknown")
                    task_type = task.get("task_type", "unknown")
                    logger.warning(f"Boss: task '{task_type}' assigned to {agent} is stale (10+ min)")

                    # Ask the agent what's happening
                    response = await ask_agent("orchestrator", agent,
                        f"You have task '{task_type}' running for 10+ minutes. What's the status? Are you stuck?",
                        timeout=10)
                    if response:
                        logger.info(f"Boss followup — {agent} says: {response.get('answer', '')[:150]}")

                # Check for agents that haven't completed any tasks recently
                for agent_name in ("titan", "clawdbot", "hermes"):
                    recent_completions = await fetch_val(
                        """SELECT COUNT(*) FROM task_queue
                           WHERE assigned_agent = %s AND status = 'completed'
                           AND created_at > NOW() - INTERVAL '30 minutes'""",
                        (agent_name,),
                    ) or 0
                    if recent_completions == 0:
                        # Check if they're alive
                        from shared.comms import is_agent_alive
                        alive = await is_agent_alive(agent_name, max_age_seconds=120)
                        if not alive:
                            logger.error(f"Boss: {agent_name} appears down — no completions, no heartbeat")
                            await send_alert(
                                f"Agent {agent_name} may be down. No task completions in 30min, no heartbeat.",
                                sender="orchestrator")

            except Exception as exc:
                logger.debug(f"Followup loop: {exc}")

            await asyncio.sleep(300)  # Check every 5 minutes


# ── Strategic priority logic (from Perseus, deterministic) ─────

def _decide_priorities(state: dict) -> dict:
    """
    Deterministic priority logic — no LLM call, just pipeline math.
    Rule: always prioritize revenue-closest work first.
    """
    schedule_now: list[str] = []
    skip_now: list[str] = []
    reasons: list[str] = []

    hot = state.get("hot_leads", 0)
    ready_close = state.get("ready_to_close", 0)
    ready_deliver = state.get("ready_to_deliver", 0)
    ready_invoice = state.get("ready_to_invoice", 0)
    outreach = state.get("outreach_active", 0)

    # Priority 1: Invoices waiting → money on the table
    if ready_invoice > 0:
        schedule_now.append("process_invoices")
        reasons.append(f"{ready_invoice} leads ready to invoice")

    # Priority 2: Sites to build → unblock invoicing
    if ready_deliver > 0:
        schedule_now.append("build_sites")
        schedule_now.append("site_verify")
        reasons.append(f"{ready_deliver} sites to build")

    # Priority 3: Hot leads → close before they cool
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

    # Priority 5: Top of funnel — only if not overwhelmed downstream
    if hot <= 3 and ready_deliver <= 2:
        schedule_now.append("lead_discovery")
        schedule_now.append("lead_research")
        schedule_now.append("enrich_leads")
    else:
        skip_now.extend(["lead_discovery", "lead_research", "enrich_leads"])
        reasons.append(f"Skipping discovery: {hot} hot + {ready_deliver} to deliver")

    # Deduplicate preserving order
    seen: set[str] = set()
    deduped = [n for n in schedule_now if n not in seen and not seen.add(n)]

    reasoning = "; ".join(reasons) if reasons else "Normal pipeline flow"
    return {"schedule": deduped, "skip": skip_now, "reasoning": reasoning}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    orchestrator = Orchestrator()
    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        logger.info("Orchestrator stopped")


if __name__ == "__main__":
    main()
