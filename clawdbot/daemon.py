"""
ClawdBot Daemon — The 4th agent. Handles skills, browser automation, scraping, and site verification.

ClawdBot is the hands of Perseus:
- Executes installed skills from all skill directories
- Runs browser automation via browser-use
- Scrapes and verifies sites via Firecrawl
- Communicates with Titan/Hermes/Perseus via shared task_queue and events
- Shares memory through Mem0 vector store and titan_learnings table
"""

import asyncio
import json
import os
import signal
import shutil
import time
from pathlib import Path

from openjarvis.vassals.registry import heartbeat
from shared import db
from shared.agent_base import AgentBase
from shared.comms import record_decision
from shared.config import config
from shared.logging_config import setup_logging
from shared.skill_loader import execute_skill, find_skill, list_installed_skills

logger = setup_logging("clawdbot")

# ── Playwright pool for V2 visual pipeline ────────────────────────────
_playwright_pool = None

try:
    from clawdbot.renderer import PlaywrightPool as _PlaywrightPoolCls
except ImportError:  # phase 34 module not yet available
    _PlaywrightPoolCls = None  # type: ignore[assignment,misc]


async def _start_playwright_pool() -> None:
    """Start the shared Playwright browser pool if V2 is enabled."""
    global _playwright_pool
    if _PlaywrightPoolCls is None:
        logger.debug("PlaywrightPool not available (renderer module missing)")
        return
    if os.environ.get(
        "CLAWDBOT_V2_ENABLED", "",
    ).lower() not in ("true", "1", "yes"):
        return
    try:
        _playwright_pool = _PlaywrightPoolCls()
        await _playwright_pool.start()
        logger.info("Playwright pool started for V2 pipeline")
        # Inject into site_builder so it can access the pool
        from clawdbot.site_builder import _set_playwright_pool
        _set_playwright_pool(_playwright_pool)
    except Exception as exc:
        logger.warning("Failed to start Playwright pool: %s", exc)
        _playwright_pool = None


async def _stop_playwright_pool() -> None:
    """Shut down the shared Playwright browser pool."""
    global _playwright_pool
    if _playwright_pool is None:
        return
    try:
        await _playwright_pool.stop()
        logger.info("Playwright pool stopped")
    except Exception as exc:
        logger.warning("Error stopping Playwright pool: %s", exc)
    finally:
        _playwright_pool = None
        try:
            from clawdbot.site_builder import _set_playwright_pool
            _set_playwright_pool(None)
        except Exception:
            pass


CORE_BOOTSTRAP_CAPABILITIES = ("browser", "scraper", "web_search")
CLAWDBOT_AGENT_MESH_SPECS = (
    {
        "name": "clawdbot-agent-orchestrator",
        "agent_type": "orchestrator",
        "config": {
            "tools": ["web_search", "browser", "shell_exec", "file_write", "code_execute"],
            "max_turns": 8,
            "temperature": 0.1,
            "max_tokens": 1400,
            "owner": "clawdbot",
            "role": "task-decomposer",
        },
    },
    {
        "name": "clawdbot-agent-network",
        "agent_type": "react",
        "config": {
            "tools": ["web_search", "browser", "shell_exec"],
            "max_turns": 6,
            "temperature": 0.2,
            "max_tokens": 1100,
            "owner": "clawdbot",
            "role": "parallel-worker",
        },
    },
    {
        "name": "clawdbot-agent-monitor",
        "agent_type": "monitor_operative",
        "config": {
            "tools": ["web_search", "shell_exec"],
            "max_turns": 5,
            "temperature": 0.0,
            "max_tokens": 900,
            "owner": "clawdbot",
            "role": "runtime-monitor",
        },
    },
)

_CHANNEL_BACKEND = None
_CHANNEL_BACKEND_KEY = None


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _has_telnyx_voice_config() -> bool:
    return all(
        os.environ.get(name, "").strip()
        for name in ("TELNYX_API_KEY", "TELNYX_CONNECTION_ID", "TELNYX_FROM_NUMBER")
    )


def _has_twilio_voice_config() -> bool:
    return all(
        os.environ.get(name, "").strip()
        for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    )


def _has_android_runtime() -> bool:
    return shutil.which("adb") is not None


def _default_android_serial(payload: dict | None = None) -> str:
    payload = payload or {}
    return (
        str(payload.get("serial", "") or "").strip()
        or os.environ.get("ADB_SERIAL", "").strip()
        or os.environ.get("ANDROID_SERIAL", "").strip()
    )


def _resolve_voice_provider(requested: str = "") -> str:
    requested = requested.strip().lower()
    if requested in {"telnyx", "twilio"}:
        return requested
    if _has_telnyx_voice_config():
        return "telnyx"
    if _has_twilio_voice_config():
        return "twilio"
    return ""


def _whatsapp_channel_enabled() -> bool:
    if _env_enabled("CLAWDBOT_ENABLE_WHATSAPP"):
        return True
    if os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip() and os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip():
        return True
    if os.environ.get("WHATSAPP_TO", "").strip():
        return True
    try:
        from openjarvis.core.config import load_config

        oj_config = load_config()
        key = (oj_config.channel.default_channel or "").strip()
        return bool(oj_config.channel.enabled and key in {"whatsapp", "whatsapp_baileys"})
    except Exception:
        return False


def _agent_mesh_entries() -> list[dict]:
    from shared.oj_bridge import get_agent_manager

    manager = get_agent_manager()
    existing = {agent["name"]: agent for agent in manager.list_agents()}
    mesh: list[dict] = []

    for spec in CLAWDBOT_AGENT_MESH_SPECS:
        current = existing.get(spec["name"])
        desired_config = dict(spec["config"])
        if current is None:
            current = manager.create_agent(
                name=spec["name"],
                agent_type=spec["agent_type"],
                config=desired_config,
            )
        else:
            merged = dict(desired_config)
            merged.update(current.get("config", {}))
            current = manager.update_agent(
                current["id"],
                agent_type=spec["agent_type"],
                config=merged,
            )

        mesh.append(
            {
                "id": current["id"],
                "name": current["name"],
                "agent_type": current["agent_type"],
                "status": current["status"],
            }
        )

    return mesh


def _get_channel_backend(channel_override: str = ""):
    global _CHANNEL_BACKEND, _CHANNEL_BACKEND_KEY

    try:
        from openjarvis.core.config import load_config
        from openjarvis.system import SystemBuilder
        from shared.oj_bridge import get_bus

        oj_config = load_config()
        channel_key = (channel_override or oj_config.channel.default_channel or "").strip()
        if not oj_config.channel.enabled or channel_key not in {"whatsapp", "whatsapp_baileys"}:
            return None, oj_config, ""

        if _CHANNEL_BACKEND is None or _CHANNEL_BACKEND_KEY != channel_key:
            original = oj_config.channel.default_channel
            oj_config.channel.default_channel = channel_key
            try:
                builder = SystemBuilder()
                _CHANNEL_BACKEND = builder._resolve_channel(oj_config, get_bus())
                _CHANNEL_BACKEND_KEY = channel_key
            finally:
                oj_config.channel.default_channel = original

        if _CHANNEL_BACKEND is not None:
            try:
                _CHANNEL_BACKEND.connect()
            except Exception:
                logger.debug("ClawdBot channel connect failed for %s", channel_key, exc_info=True)

        return _CHANNEL_BACKEND, oj_config, channel_key
    except Exception:
        logger.debug("ClawdBot failed to resolve WhatsApp channel backend", exc_info=True)
        return None, None, ""


def _resolve_whatsapp_target(channel_key: str, explicit_target: str = "") -> str:
    if explicit_target:
        return explicit_target
    generic = os.environ.get("OPENJARVIS_CHANNEL_TARGET", "").strip()
    if generic:
        return generic
    if channel_key in {"whatsapp", "whatsapp_baileys"}:
        return os.environ.get("WHATSAPP_TO", "").strip()
    return ""


async def _run_adb_command(
    adb_args: list[str],
    *,
    serial: str = "",
    input_bytes: bytes | None = None,
) -> tuple[bytes, str]:
    adb_binary = shutil.which("adb")
    if adb_binary is None:
        raise RuntimeError("adb is not installed or not on PATH")

    cmd = [adb_binary]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(adb_args)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input_bytes)
    stderr_text = (stderr or b"").decode(errors="ignore")
    if proc.returncode != 0:
        raise RuntimeError(stderr_text[:240] or f"adb exited with {proc.returncode}")
    return stdout or b"", stderr_text


class ClawdBotDaemon(AgentBase):
    name = "clawdbot"
    description = "Skills executor, browser automator, web scraper, and site verifier."

    def __init__(self):
        super().__init__()
        self._running = False
        self._cycle_interval = 15  # Check for tasks every 15 seconds
        self._capability_refresh_interval = 900
        self._last_capability_refresh = 0.0
        self._runtime_capabilities: dict[str, dict] = {}
        self._agent_mesh: list[dict] = []

    async def start(self):
        """Start ClawdBot's main loop."""
        logger.info("ClawdBot starting up...")
        await db.init_pool()
        await _start_playwright_pool()
        await self.requeue_stale_tasks()
        await self.register()
        await self._bootstrap_runtime_capabilities(force=True)
        self._stopped.clear()
        self._running = True

        # Log available skills
        skills = list_installed_skills()
        logger.info(f"ClawdBot is LIVE. {len(skills)} skills available: {[s['name'] for s in skills]}")

        try:
            while self._running:
                self.begin_work("loop:cycle")
                try:
                    paused = await db.get_config("clawdbot_paused", False)
                    if paused:
                        logger.debug("ClawdBot is paused. Waiting.")
                        await asyncio.sleep(10)
                        continue

                    await self._process_task_queue()
                    await self._think()
                    await heartbeat(self.name)

                except Exception as e:
                    logger.error(f"ClawdBot cycle error: {e}", exc_info=True)
                    await self.emit_event("clawdbot_error", {"error": str(e)})
                finally:
                    self.finish_work("loop:cycle")

                await asyncio.sleep(self._cycle_interval)
        finally:
            await self.finalize_shutdown()

    async def stop(self):
        """Gracefully stop ClawdBot."""
        logger.info("ClawdBot shutdown requested...")
        self.request_shutdown()
        drained = await self.wait_for_work_drain()
        if not drained:
            logger.warning(
                "ClawdBot shutdown timed out with %d in-flight operation(s); stale tasks will be requeued on restart",
                len(self._active_work),
            )
        await _stop_playwright_pool()
        await self.wait_until_stopped()
        logger.info("ClawdBot stopped.")

    async def health_check(self) -> dict:
        skills = list_installed_skills()
        return {
            "agent": self.name,
            "status": "running" if self._running else "stopped",
            "skills_available": len(skills),
            "managed_agents": len(self._agent_mesh),
            "runtime_capabilities": self._runtime_capabilities,
        }

    async def _bootstrap_runtime_capabilities(self, *, force: bool = False):
        now = time.time()
        if not force and now - self._last_capability_refresh < self._capability_refresh_interval:
            return

        self._last_capability_refresh = now
        await self._ensure_agent_mesh()
        await self._resolve_bootstrap_capabilities()

    async def _ensure_agent_mesh(self):
        try:
            self._agent_mesh = _agent_mesh_entries()
            await db.set_config("clawdbot_agent_mesh", self._agent_mesh)
        except Exception as exc:
            logger.warning("Failed to ensure ClawdBot agent mesh: %s", exc)

    def _desired_bootstrap_capabilities(self) -> list[str]:
        desired = list(CORE_BOOTSTRAP_CAPABILITIES)
        if _env_enabled("CLAWDBOT_ENABLE_VOICE") or _has_telnyx_voice_config() or _has_twilio_voice_config():
            desired.extend(["voice_call", "sms_outreach"])
        if _env_enabled("CLAWDBOT_ENABLE_ANDROID") or _has_android_runtime():
            desired.append("android_automation")
        if _whatsapp_channel_enabled():
            desired.append("whatsapp")
        return list(dict.fromkeys(desired))

    async def _resolve_bootstrap_capabilities(self):
        from clawdbot.capability_resolver import is_already_resolved, resolve_capability

        runtime: dict[str, dict] = {}
        for capability in self._desired_bootstrap_capabilities():
            configured = capability in CORE_BOOTSTRAP_CAPABILITIES or capability in {
                "voice_call",
                "sms_outreach",
                "android_automation",
                "whatsapp",
            }
            state = {
                "capability": capability,
                "configured": configured,
                "resolved": False,
                "method": "",
            }
            try:
                if await is_already_resolved(capability):
                    state["resolved"] = True
                    state["method"] = "already_available"
                else:
                    result = await resolve_capability(capability, {"trigger": "runtime_bootstrap"})
                    state["resolved"] = bool(result.get("resolved"))
                    state["method"] = result.get("method", "")
                    if result.get("details"):
                        state["details"] = result["details"][:200]
            except Exception as exc:
                state["error"] = str(exc)[:200]
            runtime[capability] = state

        mesh_ready = bool(self._agent_mesh)
        runtime["agent_orchestrator"] = {
            "capability": "agent_orchestrator",
            "configured": True,
            "resolved": mesh_ready,
            "method": "managed_agent_mesh" if mesh_ready else "unavailable",
            "managed_agents": [agent["name"] for agent in self._agent_mesh],
        }
        runtime["agent_network"] = {
            "capability": "agent_network",
            "configured": True,
            "resolved": mesh_ready,
            "method": "managed_agent_mesh" if mesh_ready else "unavailable",
            "managed_agents": [agent["name"] for agent in self._agent_mesh],
        }
        runtime["swarm_orchestrator"] = {
            "capability": "swarm_orchestrator",
            "configured": True,
            "resolved": mesh_ready,
            "method": "managed_agent_mesh" if mesh_ready else "unavailable",
            "managed_agents": [agent["name"] for agent in self._agent_mesh],
        }
        self._runtime_capabilities.update(runtime)
        await db.set_config("clawdbot_capability_runtime", self._runtime_capabilities)

    async def _execute_with_brain(self, task_type: str, payload: dict, default_handler) -> dict | None:
        """Route task through Opus brain if complex, or directly to handler if simple."""
        from clawdbot.brain import decide_approach, should_use_brain

        if not await should_use_brain(task_type, payload):
            return await default_handler(payload)

        # Ask Opus how to approach this
        problem = (
            f"Task: {task_type}\n"
            f"Payload: {json.dumps({k: str(v)[:200] for k, v in payload.items()})}"
        )
        decision = await decide_approach(problem, context={"task_type": task_type, **payload})
        approach = decision.get("approach", "")

        # Execute the approach Opus chose
        if approach == "skill" and decision.get("tool_name"):
            return await handle_skill_execute({
                "skill_name": decision["tool_name"],
                "prompt": payload.get("description", payload.get("prompt", "")),
                "context": payload,
                "request_id": payload.get("request_id", ""),
            })
        elif approach == "n8n_workflow" and decision.get("tool_name"):
            return await handle_n8n_workflow({
                "webhook_path": decision["tool_name"],
                "workflow_payload": payload,
                "request_id": payload.get("request_id", ""),
            })
        elif approach == "playwright":
            url = payload.get("url", "")
            desc = payload.get("description", "")
            if url:
                return await _playwright_fallback(url, desc)
            return await default_handler(payload)
        elif approach == "ask_operator":
            return await handle_service_signup({
                "service": decision.get("tool_name", "unknown"),
                "url": "",
                "purpose": decision.get("reasoning", "ClawdBot needs help"),
            })
        elif approach == "multi_step":
            # Execute steps in sequence
            results = []
            for step in decision.get("steps", []):
                action = step.get("action", "")
                params = step.get("params", {})
                step_handler = TASK_HANDLERS.get(action)
                if step_handler:
                    step_result = await step_handler({**params, "request_id": payload.get("request_id", "")})
                    results.append(step_result)
            return {"steps_completed": len(results), "results": results}
        else:
            # Opus didn't pick anything specific — fall back to default handler
            return await default_handler(payload)

    async def _think(self):
        """
        Autonomous think loop — ClawdBot looks for problems it can solve.
        Runs every cycle (~15s) but only takes action when there's something to do.
        """
        try:
            # 1. Check for recent pipeline errors I can help with
            errors = await db.fetch_all(
                """SELECT payload FROM events
                   WHERE event_type = 'pipeline_error'
                   AND acknowledged = FALSE
                   AND created_at > NOW() - INTERVAL '30 minutes'
                   ORDER BY created_at DESC LIMIT 3"""
            )

            # 2. Check for help requests from other agents
            help_requests = await db.fetch_all(
                """SELECT payload FROM events
                   WHERE event_type = 'agent_help_request'
                   AND acknowledged = FALSE
                   AND created_at > NOW() - INTERVAL '1 hour'
                   ORDER BY created_at DESC LIMIT 3"""
            )

            # 3. Check for expansion opportunities I can build
            expansions = await db.fetch_all(
                """SELECT id, title, capability_type, capability_name, smallest_step
                   FROM revenue_expansion_opportunities
                   WHERE status = 'proposed' AND capability_type IN ('skill', 'tool')
                   ORDER BY expected_roi DESC LIMIT 2"""
            )

            # Act on what we found (even if some lists are empty)
            if not errors and not help_requests and not expansions:
                # Nothing reactive — run proactive scan instead
                await self._proactive_scan()
                return

            if errors:
                logger.debug(f"Think loop: {len(errors)} recent pipeline errors to review")

            if help_requests:
                for req in help_requests:
                    await self._handle_help_request(req.get("payload", {}))

            # Check for capability_missing events — if the same need comes up 3+ times, resolve it
            await self._resolve_repeated_capability_needs()

            if expansions:
                for exp in expansions:
                    logger.info(
                        f"Think loop: expansion opportunity #{exp['id']} '{exp['title']}' "
                        f"({exp['capability_type']}: {exp['capability_name']}) — "
                        f"smallest step: {(exp.get('smallest_step') or 'not defined')[:100]}"
                    )
                    if exp["capability_type"] == "skill":
                        await self._resolve_expansion_skill(exp)

            # Always run proactive scan after reactive work
            await self._proactive_scan()
            await self._bootstrap_runtime_capabilities()

        except Exception as e:
            logger.debug(f"Think loop error (non-critical): {e}")

    # ── Proactive scanning ────────────────────────────────────────

    async def _proactive_scan(self):
        """Look at pipeline metrics and infrastructure, recommend actions to other agents."""
        try:
            # Leads stuck without email for 24h+
            stuck_no_email = await db.fetch_val(
                "SELECT COUNT(*) FROM clients WHERE (email IS NULL OR email = '') "
                "AND status = 'discovered' AND created_at < NOW() - INTERVAL '24 hours'"
            ) or 0
            if stuck_no_email > 10:
                await db.insert_task("enrich_leads", {"batch_size": min(stuck_no_email, 20), "proactive": True}, dedupe=True)
                await self._collaborate("titan", "discovery_quality",
                    f"{stuck_no_email} leads stuck without email for 24h+.")

            # Discovery returning zero results — act within 1 hour, not 6
            empty_runs = await db.fetch_val(
                "SELECT COUNT(*) FROM events WHERE event_type = 'lead_discovery_empty' "
                "AND created_at > NOW() - INTERVAL '1 hour'"
            ) or 0
            if empty_runs >= 2:
                await self._collaborate("titan", "discovery_failing",
                    f"Discovery returned zero results {empty_runs} times in 1h. "
                    "Pipeline will starve without leads.")

            # Demo QA failure rate
            qa_failures = await db.fetch_val(
                "SELECT COUNT(*) FROM events WHERE event_type = 'demo_qa_failed' "
                "AND created_at > NOW() - INTERVAL '24 hours'"
            ) or 0
            if qa_failures >= 2:
                await self._collaborate("titan", "demo_quality",
                    f"{qa_failures} demo sites failed QA in 24h. v0.dev may need different prompts.")

            # Infrastructure health checks
            await self._check_infra_health()

        except Exception as e:
            logger.debug(f"Proactive scan error (non-critical): {e}")

    async def _check_infra_health(self):
        """Lightweight infrastructure checks — alert other agents if something is down."""
        import httpx

        # Ollama
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{config.ollama.host}/api/tags")
                if resp.status_code != 200:
                    await self._recommend("perseus", "ollama_degraded",
                        f"Ollama returned {resp.status_code}. LLM fallback may be broken.")
        except Exception as e:
            await self._recommend("perseus", "ollama_down",
                f"Ollama unreachable: {str(e)[:120]}. All budget-gated LLM calls will fail.")

        # Mem0
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{config.memory.mem0_host}/v1/memories/search/",
                    json={"query": "health", "user_id": "titan", "limit": 1})
                if resp.status_code >= 500:
                    await self._recommend("titan", "mem0_degraded",
                        f"Mem0 returned {resp.status_code}. Learning storage is degraded.")
        except Exception as e:
            await self._recommend("titan", "mem0_down",
                f"Mem0 unreachable: {str(e)[:120]}. Vector memory is offline.")

        # Firecrawl
        try:
            from tools.firecrawl_client import get_firecrawl_status
            status = get_firecrawl_status()
            if status.get("mode") != "live":
                await self._recommend("titan", "firecrawl_down",
                    f"Firecrawl is not live: {status.get('summary', 'unknown')[:120]}. Discovery/research scraping degraded.")
        except Exception:
            pass

    # ── Agent collaboration — real conversations, not just recommendations ─

    async def _collaborate(self, target_agent: str, topic: str, problem: str):
        """Have a real conversation with another agent and try to fix the problem."""
        from shared.comms import ask_agent, delegate_task
        from clawdbot.brain import decide_approach

        try:
            # Dedup: don't re-collaborate on same topic within 30 minutes
            recent = await db.fetch_one(
                "SELECT id FROM agent_decisions WHERE agent = 'clawdbot' "
                "AND decision_type = 'collaboration' AND context->>'topic' = %s "
                "AND created_at > NOW() - INTERVAL '30 minutes'",
                (topic,),
            )
            if recent:
                return

            logger.info(f"Collaborating with {target_agent} on: {topic}")

            # Step 1: Ask the agent what's going on from their side
            response = await ask_agent("clawdbot", target_agent,
                f"I noticed a problem: {problem}. What's your current state on this? "
                f"What have you tried?", timeout=15)
            their_context = response.get("answer", "no response") if response else "agent unreachable"

            # Step 2: Ask Opus brain what to do with both sides of context
            from clawdbot.brain import _inventory
            tools_inventory = _inventory()
            skill_names = [s["name"] for s in tools_inventory.get("skills", [])[:15]]
            decision = await decide_approach(
                f"Problem: {problem}\n"
                f"{target_agent} says: {their_context}\n"
                f"Available skills: {', '.join(skill_names)}\n"
                f"What should I do to fix this?",
                available_tools=tools_inventory)

            approach = decision.get("approach", "ask_operator")
            tool_name = decision.get("tool_name", "")
            logger.info(f"Collaboration decision: {approach} (tool: {tool_name})")

            # Step 3: Execute the fix
            if approach == "skill" and tool_name:
                from shared.skill_loader import execute_skill
                result = await execute_skill(tool_name,
                    f"Fix this problem: {problem}. Context from {target_agent}: {their_context}",
                    {})
                logger.info(f"Skill '{tool_name}' executed for fix: {str(result)[:200]}")

            elif approach == "ask_operator":
                # Escalate through Hermes to operator, and up to the boss
                await ask_agent("clawdbot", "hermes",
                    f"Need operator help with: {problem}. "
                    f"{target_agent} says: {their_context}. "
                    f"I couldn't fix it autonomously.", timeout=10)
                # Also inform the boss
                await ask_agent("clawdbot", "orchestrator",
                    f"Escalation: {topic} — {problem}. "
                    f"Tried to fix but need human input.", timeout=10)

            elif approach in ("http_scrape", "playwright"):
                # ClawdBot can do this itself
                task_type = "web_scrape" if approach == "http_scrape" else "browser_task"
                await db.insert_task(task_type, {
                    "url": decision.get("url", ""),
                    "description": f"Fix: {problem}",
                    "proactive": True,
                }, dedupe=True)

            # Step 4: Record the collaboration
            await record_decision(
                agent="clawdbot",
                decision_type="collaboration",
                context={"target": target_agent, "topic": topic, "their_context": their_context[:200]},
                decision={"approach": approach, "tool": tool_name},
                reasoning=f"Problem: {problem}. {target_agent}: {their_context[:100]}. Fix: {approach}",
            )

        except Exception as e:
            logger.debug(f"Collaboration with {target_agent} on '{topic}' failed: {e}")
            # Fall back to old-style recommendation if collaboration fails
            await self._recommend(target_agent, topic, problem)

    async def _recommend(self, target_agent: str, topic: str, message: str):
        """Send a recommendation to another agent. Deduped by topic per hour. Fallback for _collaborate."""
        try:
            recent = await db.fetch_one(
                "SELECT id FROM agent_decisions WHERE agent = 'clawdbot' "
                "AND decision_type = 'recommendation' AND context->>'topic' = %s "
                "AND created_at > NOW() - INTERVAL '1 hour'",
                (topic,),
            )
            if recent:
                return

            await record_decision(
                agent="clawdbot",
                decision_type="recommendation",
                context={"target": target_agent, "topic": topic},
                decision={"message": message},
                reasoning=message,
            )
            await db.emit_event("agent_recommendation", {
                "from": "clawdbot",
                "to": target_agent,
                "topic": topic,
                "message": message,
            })
            logger.info(f"Recommendation to {target_agent}: {message[:100]}")
        except Exception as e:
            logger.debug(f"Recommendation failed (non-critical): {e}")

    async def _resolve_expansion_skill(self, exp: dict):
        """Try to resolve a missing expansion skill via the capability resolver."""
        from clawdbot.capability_resolver import is_already_resolved, resolve_capability

        skill_name = exp["capability_name"]
        if find_skill(skill_name):
            return  # Already installed

        # Map the skill to a capability category for resolution
        capability = _skill_to_capability(skill_name)
        if await is_already_resolved(capability):
            return

        result = await resolve_capability(capability, {
            "opportunity_id": exp["id"],
            "skill": skill_name,
            "title": exp.get("title", ""),
        })
        if result.get("resolved"):
            logger.info(
                f"Resolved capability '{capability}' for expansion #{exp['id']} via {result['method']}"
            )

    async def _handle_help_request(self, payload: dict):
        """Try to help another agent with a problem. Uses Opus brain for complex requests."""
        problem = payload.get("problem", "")
        from_agent = payload.get("from", "")

        # First try the fast keyword-based capability resolver
        from clawdbot.capability_resolver import is_already_resolved, resolve_capability
        capability = _problem_to_capability(problem)

        if capability:
            if await is_already_resolved(capability):
                return
            result = await resolve_capability(capability, {
                "from_agent": from_agent,
                "problem": problem,
            })
            if result.get("resolved"):
                logger.info(f"Resolved help request for '{capability}' via {result['method']}")
                return

        # Keyword mapping failed or resolver couldn't help — ask Opus
        from clawdbot.brain import decide_approach
        decision = await decide_approach(
            f"Agent '{from_agent}' needs help: {problem}",
            context={"from_agent": from_agent, "help_request": True},
        )

        approach = decision.get("approach", "")
        if approach == "ask_operator":
            await self._recommend(from_agent, f"help_{from_agent}",
                f"Can't solve '{problem[:80]}' autonomously. Opus suggests asking operator: {decision.get('reasoning', '')[:120]}")
        elif approach and approach != "http_scrape":
            logger.info(f"Brain suggests {approach} for help request from {from_agent}: {decision.get('reasoning', '')[:80]}")

    async def _resolve_repeated_capability_needs(self):
        """If the same capability_missing event fires 3+ times in an hour, proactively resolve."""
        from clawdbot.capability_resolver import is_already_resolved, resolve_capability

        try:
            rows = await db.fetch_all(
                """SELECT payload->>'capability' as capability, COUNT(*) as cnt
                   FROM events
                   WHERE event_type = 'capability_missing'
                   AND created_at > NOW() - INTERVAL '1 hour'
                   GROUP BY payload->>'capability'
                   HAVING COUNT(*) >= 3"""
            )
            for row in rows:
                cap = row.get("capability", "")
                if cap and not await is_already_resolved(cap):
                    logger.info(f"Think loop: proactively resolving repeated need for '{cap}'")
                    await resolve_capability(cap, {"trigger": "repeated_need", "count": row["cnt"]})
        except Exception as e:
            logger.debug(f"Repeated capability check failed: {e}")

    async def _process_task_queue(self):
        """Process pending tasks assigned to ClawdBot.

        Simple tasks go directly to their handler.
        Complex tasks route through the Opus brain first.
        """
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
                request_id = payload.get("request_id", "")

                # Ask the brain for complex tasks
                result = await self._execute_with_brain(task_type, payload, handler)
                if request_id:
                    await db.emit_event("task_result", {
                        "request_id": request_id,
                        "task_type": task_type,
                        "ok": True,
                        "result": result or {},
                    })
                await self.complete_task(task["id"])
                logger.debug(f"Task {task['id']} ({task_type}) completed")
            except Exception as e:
                await self.fail_task(task["id"], str(e))
                request_id = ""
                if isinstance(task.get("payload"), str):
                    try:
                        request_id = json.loads(task["payload"]).get("request_id", "")
                    except Exception:
                        request_id = ""
                elif isinstance(task.get("payload"), dict):
                    request_id = task["payload"].get("request_id", "")
                if request_id:
                    await db.emit_event("task_result", {
                        "request_id": request_id,
                        "task_type": task_type,
                        "ok": False,
                        "error": str(e),
                    })
                logger.error(f"Task {task['id']} ({task_type}) failed: {e}")
            finally:
                self.finish_work(work_id)


# ── Task Handlers ──────────────────────────────────────────────────

async def handle_skill_execute(payload: dict):
    """Execute a named skill with given context. Tries to resolve if missing."""
    skill_name = payload.get("skill_name", "")
    task_prompt = payload.get("prompt", "")
    context = payload.get("context", {})

    if not skill_name:
        raise ValueError("skill_name is required")

    skill_path = find_skill(skill_name)
    if not skill_path:
        # Try to resolve the capability
        from clawdbot.capability_resolver import resolve_capability
        capability = _skill_to_capability(skill_name)
        result = await resolve_capability(capability, {"skill": skill_name})
        if result.get("resolved"):
            skill_path = find_skill(skill_name)
        if not skill_path:
            raise ValueError(f"Skill '{skill_name}' not found and could not be resolved")

    # Safety: vet external skills before first execution
    from clawdbot.safety import is_skill_vetted, vet_skill
    if not await is_skill_vetted(skill_name):
        vet_result = await vet_skill(skill_path)
        if not vet_result.passed:
            raise ValueError(f"Skill '{skill_name}' blocked by safety gate: {vet_result.details[:200]}")

    skill_output = await execute_skill(skill_name, task_prompt, context)

    # Store result as event so requesting daemon can pick it up
    await db.emit_event("skill_result", {
        "skill": skill_name,
        "result": skill_output[:2000] if skill_output else "",
        "request_id": payload.get("request_id", ""),
    })

    # Store as learning if tagged
    if payload.get("store_learning"):
        await _store_learning("skill_execution", f"Skill {skill_name}: {skill_output[:500]}")

    logger.info(f"Skill '{skill_name}' executed successfully")
    return {
        "skill": skill_name,
        "result": skill_output[:2000] if skill_output else "",
    }


async def handle_web_scrape(payload: dict):
    """Scrape a URL and return extracted content."""
    url = payload.get("url", "")
    if not url:
        raise ValueError("url is required")

    from tools.firecrawl_client import scrape_url
    result = scrape_url(url)

    await db.emit_event("scrape_result", {
        "url": url,
        "result": result,
        "request_id": payload.get("request_id", ""),
    })

    logger.info(f"Scraped {url}")
    return {
        "url": url,
        "result": result,
    }


async def handle_operator_message(payload: dict):
    """Acknowledge an operator note routed to ClawdBot."""
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ValueError("operator message is required")

    await db.emit_event("agent_message_ack", {
        "agent": "clawdbot",
        "reply": "ClawdBot received your note and will use it on the next skill or automation task.",
        "operator_message": message,
        "priority": payload.get("priority", "priority"),
        "source": payload.get("source", "war_room"),
    })
    logger.info("ClawdBot received operator message: %s", message)
    return {"ok": True}


async def handle_site_verify(payload: dict):
    """Verify a deployed site is live and functional."""
    url = payload.get("url", "")
    client_id = payload.get("client_id")
    request_id = payload.get("request_id", "")

    if not url:
        raise ValueError("url is required")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            status_code = resp.status_code
            is_live = 200 <= status_code < 400
            content_length = len(resp.content)

            result = {
                "url": url,
                "status_code": status_code,
                "is_live": is_live,
                "content_length": content_length,
                "client_id": client_id,
                "request_id": request_id,
            }

            if client_id and is_live:
                await db.execute(
                    "UPDATE clients SET final_site_url = %s WHERE id = %s",
                    (url, client_id),
                )

            await db.emit_event("site_verified", result)

            if not is_live:
                await db.emit_event("site_down", {
                    "url": url,
                    "status_code": status_code,
                    "client_id": client_id,
                    "request_id": request_id,
                })

            logger.info(f"Site verify: {url} → {status_code} ({'LIVE' if is_live else 'DOWN'})")
            return result

    except Exception as e:
        await db.emit_event("site_down", {
            "url": url,
            "error": str(e),
            "client_id": client_id,
            "request_id": request_id,
        })
        raise


async def handle_browser_task(payload: dict):
    """Execute a browser automation task. Self-equips if no browser skill is installed."""
    task_description = payload.get("description", "")
    url = payload.get("url", "")

    # Try known browser skill names (includes DroidClaw for Android-based automation)
    browser_skills = [
        "agent-browser",          # OpenClaw Agent Browser (full multi-tab)
        "browser-use",            # Headless browser skill
        "droidclaw",              # Android phone as robot hands via ADB
        "browser-automation",
        "playwright-browser",
        "puppeteer",
    ]
    skill_name = None
    for name in browser_skills:
        if find_skill(name):
            skill_name = name
            break

    if not skill_name:
        # Try to resolve browser capability
        from clawdbot.capability_resolver import resolve_capability
        result = await resolve_capability("browser", {"task": task_description, "url": url})
        if result.get("resolved"):
            # Re-check after resolution
            for name in browser_skills:
                if find_skill(name):
                    skill_name = name
                    break

    if skill_name:
        skill_output = await execute_skill(skill_name, task_description, {"url": url})
        await db.emit_event("browser_result", {
            "result": skill_output[:2000] if skill_output else "",
            "request_id": payload.get("request_id", ""),
        })
        return {
            "result": skill_output[:2000] if skill_output else "",
            "url": url,
        }

    # Fallback: Playwright headless browser (self-installs if needed)
    if url:
        try:
            return await _playwright_fallback(url, task_description)
        except Exception as e:
            logger.warning(f"Playwright fallback failed: {e}")
            # Last resort: plain HTTP scrape
            return await handle_web_scrape(payload)

    raise ValueError("No browser skill available and could not be resolved")


async def handle_agent_orchestration(payload: dict):
    """Activate and use ClawdBot's OpenJarvis agent mesh."""
    objective = str(payload.get("objective", "") or "").strip()
    manager = None
    try:
        from shared.oj_bridge import get_agent_manager

        manager = get_agent_manager()
    except Exception as exc:
        raise RuntimeError(f"OpenJarvis agent manager unavailable: {exc}") from exc

    mesh = _agent_mesh_entries()
    await db.set_config("clawdbot_agent_mesh", mesh)

    if not objective:
        return {
            "status": "ready",
            "agents": mesh,
        }

    orchestrator_agent = next((agent for agent in mesh if agent["name"] == "clawdbot-agent-orchestrator"), None)
    worker_agent = next((agent for agent in mesh if agent["name"] == "clawdbot-agent-network"), None)

    created_tasks = []
    if orchestrator_agent:
        created_tasks.append(
            manager.create_task(
                orchestrator_agent["id"],
                f"Decompose and coordinate: {objective}",
            )
        )
    if worker_agent:
        created_tasks.append(
            manager.create_task(
                worker_agent["id"],
                f"Execute delegated work for: {objective}",
            )
        )

    await db.emit_event("agent_mesh_task_created", {
        "objective": objective,
        "task_count": len(created_tasks),
        "request_id": payload.get("request_id", ""),
    })
    return {
        "status": "delegated",
        "objective": objective,
        "agents": mesh,
        "tasks": created_tasks,
    }


async def handle_android_automation(payload: dict):
    """Run basic DroidClaw-style Android actions through ADB."""
    from clawdbot.capability_resolver import is_already_resolved, resolve_capability

    action = str(payload.get("action", "status") or "status").strip().lower()
    serial = _default_android_serial(payload)
    if not await is_already_resolved("android_automation"):
        await resolve_capability("android_automation", {"trigger": "android_automation", "action": action})

    if not _has_android_runtime():
        return await handle_service_signup({
            "service": "android_adb",
            "url": "https://developer.android.com/tools/adb",
            "purpose": "ClawdBot Android automation requires an ADB bridge or adbutils runtime.",
        })

    if action == "status":
        stdout, _ = await _run_adb_command(["devices"], serial="")
        lines = stdout.decode(errors="ignore").splitlines()
        devices = []
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        selected = serial or (devices[0] if devices else "")
        return {
            "status": "ready" if devices else "no_device",
            "devices": devices,
            "selected_serial": selected,
        }

    if not serial:
        raise ValueError("android serial is required for action '%s'" % action)

    if action == "tap":
        x = int(payload.get("x", 0))
        y = int(payload.get("y", 0))
        await _run_adb_command(["shell", "input", "tap", str(x), str(y)], serial=serial)
        result = {"status": "ok", "action": action, "serial": serial, "x": x, "y": y}
    elif action == "text":
        text = str(payload.get("text", "") or "").strip()
        if not text:
            raise ValueError("text is required for android text input")
        escaped = text.replace(" ", "%s")
        await _run_adb_command(["shell", "input", "text", escaped], serial=serial)
        result = {"status": "ok", "action": action, "serial": serial, "text": text}
    elif action == "swipe":
        x1 = int(payload.get("x1", 0))
        y1 = int(payload.get("y1", 0))
        x2 = int(payload.get("x2", 0))
        y2 = int(payload.get("y2", 0))
        duration_ms = int(payload.get("duration_ms", 300))
        await _run_adb_command(
            ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
            serial=serial,
        )
        result = {
            "status": "ok",
            "action": action,
            "serial": serial,
            "from": [x1, y1],
            "to": [x2, y2],
            "duration_ms": duration_ms,
        }
    elif action == "keyevent":
        keycode = str(payload.get("keycode", "") or "").strip()
        if not keycode:
            raise ValueError("keycode is required for android keyevent")
        await _run_adb_command(["shell", "input", "keyevent", keycode], serial=serial)
        result = {"status": "ok", "action": action, "serial": serial, "keycode": keycode}
    elif action == "app_start":
        package = str(payload.get("package", "") or "").strip()
        activity = str(payload.get("activity", "") or "").strip()
        if not package:
            raise ValueError("package is required for app_start")
        component = f"{package}/{activity}" if activity else package
        await _run_adb_command(["shell", "am", "start", "-n", component], serial=serial)
        result = {"status": "ok", "action": action, "serial": serial, "component": component}
    elif action == "screenshot":
        output_path = str(payload.get("output_path", "") or "").strip()
        if not output_path:
            output_path = str(Path("/tmp") / f"clawdbot-android-{int(time.time())}.png")
        image_bytes, _ = await _run_adb_command(["exec-out", "screencap", "-p"], serial=serial)
        Path(output_path).write_bytes(image_bytes)
        result = {"status": "ok", "action": action, "serial": serial, "output_path": output_path}
    else:
        raise ValueError(f"unsupported android action: {action}")

    await db.emit_event("android_automation_result", {
        **result,
        "request_id": payload.get("request_id", ""),
    })
    return result


async def handle_voice_call(payload: dict):
    """Place or queue an outbound voice call via a configured provider."""
    from clawdbot.capability_resolver import is_already_resolved, resolve_capability

    to_number = str(payload.get("to") or payload.get("phone") or "").strip()
    if not to_number:
        raise ValueError("to is required for voice_call")

    provider = _resolve_voice_provider(str(payload.get("provider", "") or ""))
    if not provider:
        return await handle_service_signup({
            "service": "telnyx_voice",
            "url": "https://telnyx.com/sign-up",
            "purpose": f"ClawdBot needs a configured voice provider to call {to_number}.",
        })

    if not await is_already_resolved("voice_call"):
        await resolve_capability("voice_call", {"trigger": "voice_call", "provider": provider, "to": to_number})

    import httpx

    if provider == "telnyx":
        api_key = os.environ.get("TELNYX_API_KEY", "").strip()
        connection_id = os.environ.get("TELNYX_CONNECTION_ID", "").strip()
        from_number = str(payload.get("from") or os.environ.get("TELNYX_FROM_NUMBER", "")).strip()
        if not (api_key and connection_id and from_number):
            return await handle_service_signup({
                "service": "telnyx_voice",
                "url": "https://telnyx.com/sign-up",
                "purpose": "Missing TELNYX_API_KEY, TELNYX_CONNECTION_ID, or TELNYX_FROM_NUMBER for voice calling.",
            })

        request_body = {
            "connection_id": connection_id,
            "to": to_number,
            "from": from_number,
        }
        if payload.get("answer_url"):
            request_body["webhook_url"] = str(payload["answer_url"])

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://api.telnyx.com/v2/calls",
                json=request_body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            provider_payload = response.json()

    elif provider == "twilio":
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        from_number = str(payload.get("from") or os.environ.get("TWILIO_FROM_NUMBER", "")).strip()
        callback_url = str(payload.get("answer_url") or os.environ.get("TWILIO_VOICE_URL", "")).strip()
        if not (account_sid and auth_token and from_number and callback_url):
            return await handle_service_signup({
                "service": "twilio_voice",
                "url": "https://www.twilio.com/try-twilio",
                "purpose": "Missing Twilio voice credentials or TWILIO_VOICE_URL for voice calling.",
            })

        async with httpx.AsyncClient(timeout=20.0, auth=(account_sid, auth_token)) as client:
            response = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
                data={
                    "To": to_number,
                    "From": from_number,
                    "Url": callback_url,
                },
            )
            response.raise_for_status()
            provider_payload = response.json()
    else:
        raise ValueError(f"Unsupported voice provider: {provider}")

    result = {
        "status": "queued",
        "provider": provider,
        "to": to_number,
        "response": provider_payload,
    }
    await db.emit_event("voice_call_requested", {
        "provider": provider,
        "to": to_number,
        "request_id": payload.get("request_id", ""),
    })
    return result


async def handle_whatsapp_message(payload: dict):
    """Send a WhatsApp message via the OpenJarvis channel layer."""
    from clawdbot.capability_resolver import is_already_resolved, resolve_capability

    message = str(payload.get("message", "") or "").strip()
    target = str(payload.get("to") or payload.get("target") or "").strip()
    if not message:
        raise ValueError("message is required")

    if not await is_already_resolved("whatsapp"):
        await resolve_capability("whatsapp", {"trigger": "whatsapp_message", "target": target})

    backend, _, channel_key = _get_channel_backend(str(payload.get("channel", "") or ""))
    if backend is None or not channel_key:
        return await handle_service_signup({
            "service": "whatsapp_channel",
            "url": "https://developers.facebook.com/docs/whatsapp",
            "purpose": "ClawdBot needs WhatsApp channel credentials to send messages.",
        })

    resolved_target = _resolve_whatsapp_target(channel_key, target)
    if not resolved_target:
        raise ValueError("No WhatsApp target configured")

    ok = await asyncio.to_thread(
        backend.send,
        resolved_target,
        message,
        conversation_id=str(payload.get("conversation_id", "") or ""),
        metadata=payload.get("metadata") or {},
    )
    result = {
        "sent": bool(ok),
        "channel": channel_key,
        "target": resolved_target,
    }
    if ok:
        await db.emit_event("whatsapp_message_sent", {
            "target": resolved_target,
            "request_id": payload.get("request_id", ""),
        })
    return result


async def _playwright_fallback(url: str, description: str) -> dict:
    """Last-resort browser automation using Playwright directly. Self-installs if needed."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as err:
        from clawdbot.capability_resolver import _pip_install, _run_post_install
        installed = await _pip_install("playwright")
        if not installed:
            raise ImportError("Could not install playwright") from err
        await _run_post_install(["playwright", "install", "chromium"])
        from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            title = await page.title()
            content = await page.content()
            text = await page.inner_text("body")
            content_length = len(content)
        finally:
            await browser.close()

        result = {
            "url": url,
            "title": title,
            "content_length": content_length,
            "text_excerpt": text[:1000],
            "method": "playwright_fallback",
            "description": description,
        }
        await db.emit_event("browser_result", {"result": str(result)[:2000]})
        logger.info(f"Playwright fallback: {url} → {title[:60]}")
        return result


async def handle_enrich_lead(payload: dict):
    """Enrich a lead with web data — find email, phone, social profiles."""
    client_id = payload.get("client_id")
    if not client_id:
        raise ValueError("client_id is required")

    lead = await db.fetch_one(
        "SELECT * FROM clients WHERE id = %s", (client_id,)
    )
    if not lead:
        raise ValueError(f"Client {client_id} not found")

    from tools.firecrawl_client import enrich_business_profile
    result = enrich_business_profile(
        business_name=lead.get("business_name", ""),
        city=lead.get("city", ""),
        industry=lead.get("industry", ""),
        website_url=lead.get("website_url", ""),
    )

    # Extract email from search results if we don't have one
    if not lead.get("email") and result.get("mode") == "live":
        from shared.llm_client import llm
        search_data = json.dumps(result.get("search_results", [])[:3])
        extraction = await llm.generate(
            f"Extract any email address from this data for {lead.get('business_name', '')}:\n{search_data}\n\n"
            "Return ONLY the email address, or 'none' if not found.",
            model="fast",
            temperature=0,
            operation="clawdbot.enrich_lead_email",
            daemon_name="clawdbot",
        )
        email = extraction.strip().lower()
        if "@" in email and email != "none":
            await db.execute(
                "UPDATE clients SET email = %s WHERE id = %s",
                (email, client_id),
            )
            logger.info(f"Enriched lead {client_id} with email: {email}")

    await db.emit_event("lead_enriched", {
        "client_id": client_id,
        "business_name": lead.get("business_name", ""),
        "request_id": payload.get("request_id", ""),
    })
    return {
        "client_id": client_id,
        "business_name": lead.get("business_name", ""),
        "profile": result,
    }


# ── Shared Memory Helpers ──────────────────────────────────────────

async def _store_learning(category: str, insight: str, confidence: float = 0.5):
    """Store a learning in the shared titan_learnings table (accessible by all daemons)."""
    await db.execute(
        """INSERT INTO titan_learnings (category, insight, confidence, source_event)
           VALUES (%s, %s, %s, 'clawdbot')""",
        (category, insight, confidence),
    )


# ── Task Handler Registry ──────────────────────────────────────────

async def handle_site_verify_batch(payload: dict):
    """Verify all deployed sites are still live (scheduled task)."""
    sites = await db.fetch_all(
        """SELECT id, final_site_url FROM clients
           WHERE status IN ('deployed', 'invoiced', 'paid')
           AND final_site_url IS NOT NULL AND final_site_url != ''
           LIMIT 20"""
    )
    for site in sites:
        try:
            await handle_site_verify({
                "url": site["final_site_url"],
                "client_id": site["id"],
            })
        except Exception as e:
            logger.error(f"Site verify failed for client {site['id']}: {e}")
    logger.info(f"Batch site verification: {len(sites)} sites checked")
    return {"checked": len(sites)}


async def handle_enrich_leads_batch(payload: dict):
    """Enrich all leads missing email (scheduled task)."""
    leads = await db.fetch_all(
        """SELECT id FROM clients
           WHERE (email IS NULL OR email = '')
           AND status = 'discovered'
           ORDER BY created_at ASC LIMIT 10"""
    )
    for lead in leads:
        try:
            await handle_enrich_lead({"client_id": lead["id"]})
        except Exception as e:
            logger.error(f"Enrich failed for client {lead['id']}: {e}")
    if leads:
        logger.info(f"Batch lead enrichment: {len(leads)} leads processed")
    return {"processed": len(leads)}


async def handle_verify_demo_site(payload: dict):
    """Verify a demo site is good enough to send to a prospect."""
    url = payload.get("url", "")
    business_name = payload.get("business_name", "")
    client_id = payload.get("client_id")

    if not url:
        return {"passed": False, "reason": "no_url"}

    import httpx

    # Step 1: Check it loads
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"passed": False, "reason": f"http_{resp.status_code}", "url": url}

            html = resp.text
            content_length = len(html)

            # Step 2: Basic content checks
            checks = {
                "loads": True,
                "has_content": content_length > 1000,
                "has_business_name": business_name.lower() in html.lower() if business_name else True,
                "not_error_page": "404" not in html[:500] and "error" not in html[:200].lower(),
            }

            passed = all(checks.values())

            result = {
                "passed": passed,
                "url": url,
                "checks": checks,
                "content_length": content_length,
                "client_id": client_id,
            }

            if not passed:
                failed = [k for k, v in checks.items() if not v]
                result["reason"] = f"failed_checks: {', '.join(failed)}"
                logger.warning(f"Demo site QA failed for {url}: {failed}")
                await db.emit_event("demo_qa_failed", result)
            else:
                logger.info(f"Demo site QA passed for {url}")

            return result

    except Exception as e:
        return {"passed": False, "reason": f"error: {str(e)[:200]}", "url": url}


# ── Capability Mapping Helpers ─────────────────────────────────────

def _skill_to_capability(skill_name: str) -> str:
    """Map a skill name to a capability category for the resolver."""
    from clawdbot.capabilities import CAPABILITY_MAP
    skill_lower = skill_name.lower()
    for capability, strategies in CAPABILITY_MAP.items():
        for known_skill in strategies.get("skills", []):
            if known_skill.lower() == skill_lower:
                return capability
    # Fuzzy fallback: check if the skill name contains a capability keyword
    for capability in CAPABILITY_MAP:
        if capability.replace("_", "-") in skill_lower or capability.replace("_", "") in skill_lower:
            return capability
    return skill_name  # Use the skill name itself as the capability


def _problem_to_capability(problem: str) -> str:
    """Map a help request problem description to a capability."""
    problem_lower = problem.lower()
    keyword_map = {
        # World access
        "browser": "browser",
        "scrape": "scraper",
        "search": "web_search",
        "tavily": "web_search",
        "exa": "web_search",
        # Revenue pipeline
        "email find": "email_finder",
        "contact info": "email_finder",
        "phone": "phone",
        "call": "phone",
        "lead": "lead_generation",
        # Building and code
        "build": "code_builder",
        "code": "code_builder",
        "create skill": "skill_creator",
        "new skill": "skill_creator",
        # Multi-agent
        "orchestrat": "agent_orchestrator",
        "parallel": "agent_network",
        "load balanc": "agent_optimizer",
        "route task": "agent_optimizer",
        # Evolution
        "tune prompt": "capability_evolver",
        "auto-tune": "capability_evolver",
        "optimize": "capability_evolver",
        "memory": "self_improving",
        "feedback loop": "self_improving",
        "self improv": "self_improving",
        # Safety
        "security": "agent_sentinel",
        "safety": "agent_sentinel",
        "circuit break": "agent_sentinel",
        "vet skill": "skill_vetter",
        # Integrations
        "google": "google_workspace",
        "gmail": "google_workspace",
        "calendar": "google_workspace",
        "n8n": "n8n_workflow",
        "workflow": "n8n_workflow",
        "automation": "n8n_workflow",
        # Ops
        "briefing": "mission_control",
        "dashboard": "mission_control",
        "status": "mission_control",
        # Claw ecosystem
        "voice": "voice_call",
        "phone call": "voice_call",
        "clawdtalk": "voice_call",
        "telnyx": "voice_call",
        "android": "android_automation",
        "droidclaw": "android_automation",
        "adb": "android_automation",
        "mobile": "mobile_agent",
        "zeroclaw": "mobile_agent",
        "swarm": "swarm_orchestrator",
        "swarmclaw": "swarm_orchestrator",
        "delegate": "swarm_orchestrator",
        "sms": "sms_outreach",
        "text message": "sms_outreach",
        "whatsapp": "whatsapp",
        "screenshot": "screenshot_qa",
        "visual": "screenshot_qa",
        "logo": "image_generation",
        "image": "image_generation",
        "graphic": "image_generation",
        "banner": "image_generation",
        "icon": "image_generation",
        "recraft": "image_generation",
        "notebook": "notebooklm",
        "podcast": "notebooklm",
        "infographic": "notebooklm",
        "slide deck": "notebooklm",
        "mind map": "notebooklm",
        "research report": "notebooklm",
        "tts": "voice_synth",
        "text to speech": "voice_synth",
        "elevenlabs": "voice_synth",
    }
    for keyword, capability in keyword_map.items():
        if keyword in problem_lower:
            return capability
    return ""


async def handle_n8n_workflow(payload: dict):
    """Trigger an N8N workflow via webhook. ClawdBot as orchestrator."""
    webhook_path = payload.get("webhook_path", "")
    workflow_payload = payload.get("workflow_payload", {})

    if not webhook_path:
        raise ValueError("webhook_path is required (e.g. /webhook/lead-enrichment)")

    from tools.n8n_client import get_n8n_status, trigger_workflow

    status = get_n8n_status()
    if not status.get("available"):
        raise RuntimeError(f"N8N is not available: {status.get('summary', 'unknown')}")

    result = await trigger_workflow(webhook_path, workflow_payload)

    await db.emit_event("n8n_workflow_completed", {
        "webhook_path": webhook_path,
        "ok": result.get("ok", False),
        "request_id": payload.get("request_id", ""),
    })

    if result.get("ok"):
        logger.info(f"N8N workflow {webhook_path} completed successfully")
    else:
        logger.warning(f"N8N workflow {webhook_path} failed: {result.get('error', 'unknown')}")

    return result


async def handle_image_generation(payload: dict):
    """Generate images via Recraft AI for client websites, demos, and marketing."""
    from tools.recraft_client import (
        generate_hero_image,
        generate_image,
        generate_logo,
        generate_social_graphic,
        get_recraft_status,
    )

    status = get_recraft_status()
    if not status.get("available"):
        raise RuntimeError(f"Recraft not available: {status.get('summary', '')}")

    image_type = payload.get("type", "image")
    business_name = payload.get("business_name", "")
    industry = payload.get("industry", "")
    city = payload.get("city", "")

    if image_type == "logo":
        result = await generate_logo(business_name, industry)
    elif image_type == "hero":
        result = await generate_hero_image(business_name, industry, city)
    elif image_type == "social":
        result = await generate_social_graphic(business_name, payload.get("text", ""))
    else:
        result = await generate_image(payload.get("prompt", f"Professional image for {business_name}"))

    if result.get("url"):
        # Record the spend (~$0.01 per image)
        from datetime import date

        from shared.db import execute
        await execute(
            """INSERT INTO budget_tracking (month, category, amount, description, client_id, pipeline_stage)
               VALUES (%s, 'recraft_api', 0.01, %s, %s, %s)""",
            (date.today().replace(day=1), f"recraft:{image_type}", payload.get("client_id"), "image_generation"),
        )
        logger.info(f"Generated {image_type} image for {business_name}: {result['url'][:80]}")
    else:
        logger.warning(f"Recraft {image_type} generation failed: {result.get('error', 'unknown')}")

    return result


async def handle_notebooklm(payload: dict):
    """Generate NotebookLM artifacts: briefings, research, client deliverables."""
    from tools.notebooklm_client import (
        create_briefing_notebook,
        create_client_deliverable,
        create_market_research_notebook,
        get_notebooklm_status,
    )

    status = get_notebooklm_status()
    if not status.get("available"):
        # Self-equip
        from clawdbot.capability_resolver import _pip_install
        installed = await _pip_install("notebooklm-py")
        if not installed:
            raise RuntimeError("NotebookLM not available and could not be installed")

    task_type = payload.get("notebook_type", "briefing")

    if task_type == "briefing":
        result = await create_briefing_notebook(
            pipeline_data=payload.get("pipeline_data", {}),
            learnings=payload.get("learnings", []),
            metrics=payload.get("metrics", {}),
        )
    elif task_type == "market_research":
        result = await create_market_research_notebook(
            industry=payload.get("industry", ""),
            region=payload.get("region", ""),
            competitor_urls=payload.get("competitor_urls", []),
        )
    elif task_type == "client_deliverable":
        result = await create_client_deliverable(
            business_name=payload.get("business_name", ""),
            industry=payload.get("industry", ""),
            site_url=payload.get("site_url", ""),
            research_summary=payload.get("research_summary", ""),
        )
    else:
        raise ValueError(f"Unknown notebook_type: {task_type}")

    await db.emit_event("notebooklm_generated", {
        "type": task_type,
        "notebook_id": result.get("notebook_id", ""),
        "audio_url": result.get("audio_url", ""),
        "infographic_url": result.get("infographic_url", ""),
        "request_id": payload.get("request_id", ""),
    })

    logger.info(f"NotebookLM {task_type}: notebook {result.get('notebook_id', 'unknown')}")
    return result


async def handle_service_signup(payload: dict):
    """Request operator to sign up for a service ClawdBot needs.

    ClawdBot NEVER creates accounts autonomously — it identifies the need
    and asks Nico to provide the API key.
    """
    service = payload.get("service", "")
    url = payload.get("url", "")
    purpose = payload.get("purpose", "")

    if not service:
        raise ValueError("service name is required")

    await db.emit_event("service_signup_needed", {
        "service": service,
        "url": url,
        "purpose": purpose,
        "message": (
            f"ClawdBot needs a {service} account for: {purpose}. "
            f"Please sign up at {url} and provide the API key."
        ),
    })
    logger.info(f"Requested operator signup for {service}: {purpose}")
    return {"status": "awaiting_operator", "service": service}


TASK_HANDLERS = {
    "clawdbot_operator_message": handle_operator_message,
    "agent_orchestration": handle_agent_orchestration,
    "android_automation": handle_android_automation,
    "skill_execute": handle_skill_execute,
    "web_scrape": handle_web_scrape,
    "verify_single_site": handle_site_verify,
    "verify_demo_site": handle_verify_demo_site,
    "browser_task": handle_browser_task,
    "enrich_lead": handle_enrich_lead,
    "service_signup": handle_service_signup,
    "voice_call": handle_voice_call,
    "whatsapp_message": handle_whatsapp_message,
    "image_generation": handle_image_generation,
    "notebooklm": handle_notebooklm,
    "n8n_workflow": handle_n8n_workflow,
    # Scheduled batch tasks (from Perseus scheduler)
    "site_verify": handle_site_verify_batch,
    "enrich_leads": handle_enrich_leads_batch,
}


async def main():
    """Entry point for ClawdBot daemon."""
    bot = ClawdBotDaemon()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))

    await bot.start()


async def main_with_a2a():
    """Entry point for ClawdBot daemon + A2A server."""
    import os
    import uvicorn as _uvicorn
    from clawdbot.a2a_server import create_clawdbot_a2a

    bot = ClawdBotDaemon()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))

    a2a_app = create_clawdbot_a2a(bot)
    a2a_port = int(os.getenv("CLAWDBOT_A2A_PORT", "9003"))
    uvi_config = _uvicorn.Config(a2a_app, host="0.0.0.0", port=a2a_port, log_level="warning")
    server = _uvicorn.Server(uvi_config)

    logger.info("ClawdBot A2A server starting on :%d", a2a_port)
    await asyncio.gather(bot.start(), server.serve())


if __name__ == "__main__":
    import os
    if os.getenv("CLAWDBOT_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        asyncio.run(main())
