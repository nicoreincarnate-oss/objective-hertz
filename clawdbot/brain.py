"""
ClawdBot's orchestration brain — powered by Claude Opus.

When ClawdBot faces a non-trivial problem, it asks Opus to decide the best
approach from its available tools. This is the "thinking" layer that turns
ClawdBot from a task executor into an autonomous problem solver.

Opus is only called for orchestration decisions — not for every task.
Simple, well-defined tasks (scrape URL, verify site) go directly to their handler.
Complex, ambiguous, or multi-step problems go through the brain.
"""

import json
import logging

from shared.llm_client import llm
from shared.comms import record_decision
from shared.skill_loader import list_installed_skills, find_skill

logger = logging.getLogger("perseus.clawdbot.brain")


async def decide_approach(
    problem: str,
    context: dict | None = None,
    available_tools: dict | None = None,
) -> dict:
    """Ask Claude Opus how to solve a problem given ClawdBot's available tools.

    Returns:
        {
            "approach": "skill" | "n8n_workflow" | "playwright" | "http_scrape" | "enrich" | "ask_operator" | "multi_step",
            "tool_name": "specific-skill-name" or "webhook-path",
            "reasoning": "why this approach",
            "steps": [{"action": "...", "params": {...}}, ...],  # for multi_step
        }
    """
    if available_tools is None:
        available_tools = _inventory()

    prompt = f"""You are ClawdBot's orchestration brain. You decide HOW to solve problems.
You are a specialist execution brain working alongside Hermes and the rest of Perseus.

PROBLEM:
{problem}

CONTEXT:
{json.dumps(context or {}, indent=2)}

YOUR AVAILABLE TOOLS:
{json.dumps(available_tools, indent=2)}

RULES:
1. Pick the simplest tool that solves the problem.
2. If a specific skill exists for this exact task, use it.
3. If the task needs multiple API calls in sequence, use n8n_workflow.
4. If the task needs a real browser (login, JS rendering, screenshots), use playwright.
5. If it's a simple page fetch, use http_scrape (cheapest).
6. If you need an API key or account you don't have, use ask_operator.
7. For complex multi-step problems, return a steps array.
8. Never hallucinate tools that aren't in the list.
9. Work with Hermes when that improves business outcomes. Hermes owns operator communication and coordination; you own specialist execution.
10. Escalate clearly when your findings should change priorities, approvals, risk posture, or customer communication.

Return JSON only:
{{
    "approach": "skill|n8n_workflow|playwright|http_scrape|enrich|ask_operator|multi_step",
    "tool_name": "specific name if applicable",
    "reasoning": "one sentence why",
    "steps": []
}}"""

    result = await llm.generate(
        prompt,
        model="genius",
        temperature=0.2,
        max_tokens=500,
        pipeline_stage="clawdbot_orchestration",
    )

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        decision = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        decision = {
            "approach": "http_scrape",
            "tool_name": "",
            "reasoning": "Could not parse Opus response, falling back to simplest tool.",
            "steps": [],
        }

    # Record the decision for auditability
    try:
        await record_decision(
            agent="clawdbot",
            decision_type="orchestration",
            context={"problem": problem, **(context or {})},
            decision=decision,
            reasoning=decision.get("reasoning", ""),
        )
    except Exception:
        pass

    logger.info(
        f"Brain decided: {decision.get('approach')} "
        f"({'→ ' + decision.get('tool_name', '') if decision.get('tool_name') else ''}) "
        f"— {decision.get('reasoning', '')[:80]}"
    )
    return decision


async def should_use_brain(task_type: str, payload: dict) -> bool:
    """Decide if this task is complex enough to warrant an Opus call.

    Simple well-defined tasks go straight to their handler. Only ambiguous or
    multi-faceted problems get routed through the brain.
    """
    # These tasks are always simple — direct handler, no brain needed
    SIMPLE_TASKS = {
        "web_scrape",
        "verify_single_site",
        "verify_demo_site",
        "site_verify",
        "enrich_leads",
        "clawdbot_operator_message",
    }
    if task_type in SIMPLE_TASKS:
        return False

    # Browser tasks with a URL and clear description — might be simple
    if task_type == "browser_task" and payload.get("url") and len(payload.get("description", "")) < 100:
        return False

    # Skill execution where the skill is already installed — simple
    if task_type == "skill_execute" and find_skill(payload.get("skill_name", "")):
        return False

    # Everything else: ask the brain
    return True


def _inventory() -> dict:
    """Build an inventory of everything ClawdBot can use right now."""
    skills = list_installed_skills()
    skill_names = [s["name"] for s in skills]

    from clawdbot.capabilities import CAPABILITY_MAP
    capabilities = list(CAPABILITY_MAP.keys())

    # Check which integrations are live
    integrations = {}

    try:
        from tools.n8n_client import get_n8n_status
        n8n = get_n8n_status()
        integrations["n8n"] = {"available": n8n.get("available", False), "summary": n8n.get("summary", "")}
    except Exception:
        integrations["n8n"] = {"available": False, "summary": "N8N client not importable"}

    try:
        from tools.firecrawl_client import get_firecrawl_status
        fc = get_firecrawl_status()
        integrations["firecrawl"] = {"available": fc.get("available", False), "summary": fc.get("summary", "")}
    except Exception:
        integrations["firecrawl"] = {"available": False}

    # Check if Playwright is installed
    try:
        import playwright  # noqa: F401
        integrations["playwright"] = {"available": True, "summary": "Headless Chromium browser"}
    except ImportError:
        integrations["playwright"] = {"available": False, "summary": "Not installed (will self-install on first use)"}

    return {
        "installed_skills": skill_names[:30],
        "capability_categories": capabilities,
        "integrations": integrations,
        "task_handlers": [
            "skill_execute", "web_scrape", "browser_task", "enrich_lead",
            "n8n_workflow", "service_signup", "verify_single_site", "verify_demo_site",
        ],
    }
