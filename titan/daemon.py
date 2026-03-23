"""
Titan Daemon — The revenue engine main loop.
Runs continuously, processing leads through the full pipeline.
"""

import asyncio
import json
import signal

from openjarvis.vassals.registry import heartbeat
from shared import db
from shared.agent_base import AgentBase
from shared.logging_config import setup_logging
from titan.deliverability import monitor_deliverability
from titan.expansion import review_revenue_expansion
from titan.memory import daily_reflection, weekly_strategy_review
from titan.pipeline.build_site import build_sites
from titan.pipeline.close_deal import process_interested_leads
from titan.pipeline.deploy_site import deploy_sites
from titan.pipeline.email_compose import compose_emails
from titan.pipeline.email_send import send_emails, sync_campaign_analytics
from titan.pipeline.follow_up import process_follow_ups
from titan.pipeline.invoice import process_invoices
from titan.pipeline.lead_discovery import discover_leads
from titan.pipeline.lead_research import research_leads
from titan.training import run_lora_training

logger = setup_logging("titan")


async def _handle_health_check():
    """System-wide health check — verify all agents are alive, emit status."""
    from openjarvis.vassals.registry import check_agent_health
    agents = await check_agent_health()
    await db.emit_event("health_report", {"agents": agents})
    logger.debug(f"Health check: {agents}")


async def _handle_budget_check():
    """Run budget enforcement and emit status."""
    try:
        from tools.budget_guard import BudgetGuard
        guard = BudgetGuard()
        status = await guard.check_budget()
        await db.emit_event("budget_report", status)
        if status.get("exceeded"):
            logger.warning("Budget exceeded!")
    except (ImportError, Exception) as e:
        logger.debug(f"Budget check skipped: {e}")


async def _handle_morning_briefing():
    """Emit morning_briefing event — Hermes picks it up and sends to Nico."""
    await db.emit_event("morning_briefing", {"trigger": "scheduled"})
    logger.info("Morning briefing event emitted for Hermes")


async def _handle_sleep_cycle():
    """Run the nightly sleep cycle — contrarian Opus debate + backprop."""
    from openjarvis.vassals.sleep_cycle import run_sleep_cycle
    result = await run_sleep_cycle()
    logger.info(f"Sleep cycle complete: {result}")


async def _handle_operator_message(payload: dict):
    """Acknowledge an operator note routed from the War Room."""
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ValueError("operator message is required")

    await db.emit_event(
        "agent_message_ack",
        {
            "agent": "titan",
            "reply": "Titan received your note and queued it for the next cycle.",
            "operator_message": message,
            "priority": payload.get("priority", "priority"),
            "source": payload.get("source", "war_room"),
        },
    )
    logger.info("Titan received operator message: %s", message)


# Map task_queue task types to pipeline functions (direct references, not lambdas)
TASK_HANDLERS = {
    "lead_discovery": discover_leads,
    "lead_research": research_leads,
    "email_compose": compose_emails,
    "email_send": send_emails,
    "follow_up_check": process_follow_ups,
    "close_interested": process_interested_leads,
    "build_sites": build_sites,
    "process_invoices": process_invoices,
    "sync_analytics": sync_campaign_analytics,
    "deliverability_check": monitor_deliverability,
    "daily_reflection": daily_reflection,
    "weekly_strategy": weekly_strategy_review,
    "revenue_expansion_review": review_revenue_expansion,
    "sleep_cycle": _handle_sleep_cycle,
    "lora_training": run_lora_training,
    "health_check": _handle_health_check,
    "budget_check": _handle_budget_check,
    "morning_briefing": _handle_morning_briefing,
    "titan_operator_message": _handle_operator_message,
}


class TitanDaemon(AgentBase):
    name = "titan"
    description = "Autonomous revenue engine — discovers leads, sends emails, closes deals, builds sites."

    def __init__(self):
        super().__init__()
        self._running = False
        self._cycle_interval = 30  # seconds between task queue checks

    async def start(self):
        """Start Titan's main loop — polls task_queue from Perseus + runs pipeline."""
        logger.info("Titan starting up...")
        await db.init_pool()
        from titan.compliance import assert_compliance_ready
        await assert_compliance_ready()
        await self.requeue_stale_tasks()
        await self.register()
        self._stopped.clear()
        self._running = True

        logger.info("Titan is LIVE. Listening for tasks from Perseus.")
        try:
            while self._running:
                self.begin_work("loop:cycle")
                try:
                    # Check if paused
                    paused = await db.get_config("titan_paused", False)
                    if paused:
                        logger.debug("Titan is paused. Waiting.")
                        await asyncio.sleep(10)
                        continue

                    # Process tasks from Perseus scheduler
                    await self._process_task_queue()

                    # Also run the full pipeline cycle (Titan is self-driven too)
                    await self._run_pipeline_cycle()

                    # Heartbeat so Perseus knows we're alive
                    await heartbeat(self.name)

                except Exception as e:
                    logger.error(f"Titan cycle error: {e}", exc_info=True)
                    await self.emit_event("titan_error", {"error": str(e)})
                finally:
                    self.finish_work("loop:cycle")

                await asyncio.sleep(self._cycle_interval)
        finally:
            await self.finalize_shutdown()

    async def stop(self):
        """Gracefully stop Titan."""
        logger.info("Titan shutdown requested...")
        self.request_shutdown()
        drained = await self.wait_for_work_drain()
        if not drained:
            logger.warning(
                "Titan shutdown timed out with %d in-flight operation(s); stale tasks will be requeued on restart",
                len(self._active_work),
            )
        await self.wait_until_stopped()
        logger.info("Titan stopped.")

    async def health_check(self) -> dict:
        return {
            "agent": self.name,
            "status": "running" if self._running else "stopped",
        }

    async def _process_task_queue(self):
        """Process pending tasks dispatched by Perseus."""
        tasks = await self.get_pending_tasks()
        for task in tasks:
            if self._shutdown_requested:
                break
            task_type = task["task_type"]
            handler = TASK_HANDLERS.get(task_type)
            if not handler:
                continue  # Not a Titan task

            claimed = await self.claim_task(task["id"])
            if not claimed:
                continue  # Another agent got it

            work_id = f"task:{task['id']}"
            self.begin_work(work_id)
            try:
                payload = task.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if task_type == "titan_operator_message":
                    await handler(payload)
                else:
                    await handler()
                await self.complete_task(task["id"])
                logger.debug(f"Task {task['id']} ({task_type}) completed")
            except Exception as e:
                await self.fail_task(task["id"], str(e))
                logger.error(f"Task {task['id']} ({task_type}) failed: {e}")
            finally:
                self.finish_work(work_id)

    async def _consume_recommendations(self) -> None:
        """Read and ACT on recommendations from other agents."""
        try:
            from shared.comms import get_pending_recommendations, send_alert, delegate_task
            recs = await get_pending_recommendations("titan", since_minutes=30, limit=5)
            for rec in recs:
                payload = rec.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                topic = payload.get("topic", "")
                message = payload.get("message", "")
                from_agent = payload.get("from", "unknown")
                logger.info(f"Recommendation from {from_agent}: [{topic}] {message[:200]}")

                # ACT on the recommendation based on topic
                if topic == "discovery_failing":
                    await db.set_config("discovery_strategy_override", message[:200])
                    logger.warning(f"Acting: switching discovery strategy per {from_agent}")
                elif topic == "discovery_quality":
                    await delegate_task("titan", "clawdbot", "capability_resolve",
                        {"capability": "lead_discovery", "problem": message}, priority=2)
                    logger.warning(f"Acting: delegated discovery fix to ClawdBot")
                elif topic == "demo_quality":
                    await db.set_config("proposals_paused_reason", message[:200])
                    logger.warning(f"Acting: paused proposals until demo quality fixed")
                elif topic.startswith("help_"):
                    await send_alert(f"Agent needs human help: {message[:300]}", sender="titan")
                elif topic in ("ollama_down", "ollama_degraded", "firecrawl_down", "mem0_down"):
                    logger.warning(f"Infra alert from {from_agent}: {topic} — {message[:100]}")

                await self.emit_event("recommendation_consumed", {
                    "from": from_agent, "topic": topic, "consumed_by": "titan", "acted": True,
                })
        except Exception as e:
            logger.debug(f"Recommendation check: {e}")

    async def _ask_team_for_help(self, stage_name: str, error: Exception) -> None:
        """Ask ClawdBot and Hermes for help when a pipeline stage fails."""
        from shared.comms import ask_agent, delegate_task
        error_msg = str(error)[:300]

        try:
            # Ask ClawdBot: can you fix this?
            clawdbot_answer = await ask_agent("titan", "clawdbot",
                f"Pipeline stage '{stage_name}' failed: {error_msg}. "
                f"Can you fix this? What should I try differently?",
                context={"stage": stage_name, "error": error_msg},
                timeout=20)

            if clawdbot_answer and clawdbot_answer.get("answer", ""):
                answer = clawdbot_answer["answer"]
                logger.info(f"ClawdBot says about '{stage_name}' failure: {answer[:200]}")
                # If ClawdBot suggests it can fix it, delegate
                if any(kw in answer.lower() for kw in ("i can", "retry", "fix", "install", "try")):
                    await delegate_task("titan", "clawdbot", "skill_execute",
                        {"skill_name": "troubleshoot",
                         "prompt": f"Fix pipeline stage '{stage_name}': {error_msg}\nClawdBot plan: {answer[:500]}"},
                        priority=2)
                    logger.info(f"Delegated '{stage_name}' fix to ClawdBot")

            # Ask Hermes: does the operator have context?
            hermes_answer = await ask_agent("titan", "hermes",
                f"Pipeline stage '{stage_name}' failed: {error_msg}. "
                f"Does the operator have any context or instructions about this?",
                timeout=10)

            if hermes_answer and hermes_answer.get("answer", ""):
                answer = hermes_answer["answer"]
                if "no relevant" not in answer.lower() and "no context" not in answer.lower():
                    logger.info(f"Hermes has operator context for '{stage_name}': {answer[:200]}")

        except Exception as help_err:
            logger.debug(f"Team help-seeking for '{stage_name}': {help_err}")

    async def _run_pipeline_cycle(self):
        """One full cycle of the pipeline. Checks infra health before each stage."""
        # Consume ClawdBot recommendations
        await self._consume_recommendations()

        # Check infrastructure health — skip stages whose dependencies are down
        infra = await db.get_config("infra_health", {})

        stages = [
            ("discover", discover_leads, ["ollama"]),
            ("research", research_leads, ["ollama"]),
            ("compose", compose_emails, ["ollama"]),
            ("send", send_emails, ["instantly"]),
            ("follow_up", process_follow_ups, ["instantly", "ollama"]),
            ("close", process_interested_leads, ["ollama"]),
            ("build", build_sites, []),
            ("deploy", deploy_sites, []),
            ("invoice", process_invoices, []),
        ]

        for stage_name, stage_fn, required_services in stages:
            if self._shutdown_requested:
                break

            # Check required services
            skip = False
            for svc in required_services:
                from openjarvis.vassals.infra_health import is_service_ok
                if not is_service_ok(infra, svc):
                    logger.warning(f"Skipping stage '{stage_name}': {svc} is down")
                    skip = True
                    break
            if skip:
                continue

            work_id = f"stage:{stage_name}"
            self.begin_work(work_id)
            try:
                await stage_fn()
            except Exception as e:
                logger.error(f"Stage '{stage_name}' failed: {e}")
                await self.emit_event("pipeline_stage_error", {
                    "stage": stage_name,
                    "error": str(e),
                })
                # Ask teammates for help instead of just logging
                await self._ask_team_for_help(stage_name, e)
            finally:
                self.finish_work(work_id)


async def main():
    """Entry point for Titan daemon."""
    titan = TitanDaemon()

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(titan.stop()))

    await titan.start()


async def main_with_a2a():
    """Entry point for Titan daemon + A2A server."""
    import os
    import uvicorn
    from titan.a2a_server import create_titan_a2a

    titan = TitanDaemon()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(titan.stop()))

    a2a_app = create_titan_a2a(titan)
    a2a_port = int(os.environ.get("TITAN_A2A_PORT", "9001"))
    config = uvicorn.Config(a2a_app, host="0.0.0.0", port=a2a_port, log_level="warning")
    server = uvicorn.Server(config)

    logger.info("Titan A2A server starting on :%d", a2a_port)
    await asyncio.gather(titan.start(), server.serve())


if __name__ == "__main__":
    import os
    if os.environ.get("TITAN_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        asyncio.run(main())
