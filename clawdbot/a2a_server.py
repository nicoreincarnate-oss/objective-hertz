"""ClawdBot A2A Server — exposes all ClawdBot capabilities via OpenJarvis A2A protocol.

Capabilities: skill execution, browser automation, scraping, site building,
lead enrichment, image generation, safety vetting, N8N workflows, and more.
"""

from __future__ import annotations

import json
import logging

from shared import db
from shared.a2a_wrapper import AgentCard, create_a2a_app
from shared.skill_loader import list_installed_skills, find_skill

logger = logging.getLogger("perseus.clawdbot.a2a")


import os as _os

_V2_ENABLED = _os.environ.get(
    "CLAWDBOT_V2_ENABLED", "",
).lower() in ("true", "1", "yes")

_V2_CAPABILITIES = [
    "site_build_v2",
    "section_plan",
    "fullpage_qa",
    "visual_score",
] if _V2_ENABLED else []

CLAWDBOT_CARD = AgentCard(
    name="clawdbot",
    description=(
        "The hands of Perseus — skills executor, browser automator, "
        "web scraper, site builder/verifier, lead enricher, image "
        "generator, N8N workflow trigger. "
        + (
            "V2 visual production pipeline active. "
            if _V2_ENABLED
            else ""
        )
        + "26+ capability categories with self-equipping resolver "
        "and safety vetting gate."
    ),
    url="http://localhost:9003",
    version="2.0.0" if _V2_ENABLED else "1.0.0",
    capabilities=[
        "ask",
        "skill_execute", "skill_list", "skill_find",
        "web_scrape", "scrape_company",
        "browser_task",
        "agent_orchestration",
        "android_automation",
        "site_verify", "site_verify_batch", "verify_demo_site",
        "enrich_lead", "enrich_leads_batch",
        "voice_call",
        "whatsapp_message",
        "image_generation",
        "notebooklm",
        "n8n_workflow",
        "safety_vet",
        "capability_list", "capability_resolve",
        "capability_runtime_status",
        "operator_message",
        "recommend",
        "infra_health",
        "health_check",
        "events_recent",
        "event_relay",
        *_V2_CAPABILITIES,
    ],
)


# ── Capability handlers ───────────────────────────────────────────────

async def _skill_list(**_) -> list:
    return list_installed_skills()


async def _skill_find(name: str = "", **_) -> dict:
    path = find_skill(name)
    return {"name": name, "found": path is not None, "path": str(path) if path else None}


async def _skill_execute(skill_name: str = "", prompt: str = "", context: dict | None = None, **_) -> dict:
    from clawdbot.daemon import handle_skill_execute
    return await handle_skill_execute({"skill_name": skill_name, "prompt": prompt, "context": context or {}})


async def _web_scrape(url: str = "", **_) -> dict:
    from clawdbot.daemon import handle_web_scrape
    return await handle_web_scrape({"url": url})


async def _scrape_company(company_url: str = "", business_name: str = "", **_) -> dict:
    from tools.firecrawl_client import enrich_business_profile
    return enrich_business_profile(business_name=business_name, website_url=company_url)


async def _browser_task(description: str = "", url: str = "", **_) -> dict:
    from clawdbot.daemon import handle_browser_task
    return await handle_browser_task({"description": description, "url": url})


async def _agent_orchestration(objective: str = "", **kwargs) -> dict:
    from clawdbot.daemon import handle_agent_orchestration
    return await handle_agent_orchestration({"objective": objective, **kwargs})


async def _android_automation(action: str = "status", **kwargs) -> dict:
    from clawdbot.daemon import handle_android_automation
    return await handle_android_automation({"action": action, **kwargs})


async def _site_verify(url: str = "", client_id: int | None = None, **_) -> dict:
    from clawdbot.daemon import handle_site_verify
    return await handle_site_verify({"url": url, "client_id": client_id})


async def _site_verify_batch(**_) -> dict:
    from clawdbot.daemon import handle_site_verify_batch
    return await handle_site_verify_batch({})


async def _verify_demo_site(url: str = "", business_name: str = "", client_id: int | None = None, **_) -> dict:
    from clawdbot.daemon import handle_verify_demo_site
    return await handle_verify_demo_site({"url": url, "business_name": business_name, "client_id": client_id})


async def _enrich_lead(client_id: int = 0, **_) -> dict:
    from clawdbot.daemon import handle_enrich_lead
    return await handle_enrich_lead({"client_id": client_id})


async def _enrich_leads_batch(**_) -> dict:
    from clawdbot.daemon import handle_enrich_leads_batch
    return await handle_enrich_leads_batch({})


async def _image_generation(type: str = "image", business_name: str = "", industry: str = "", **kwargs) -> dict:
    from clawdbot.daemon import handle_image_generation
    return await handle_image_generation({"type": type, "business_name": business_name, "industry": industry, **kwargs})


async def _notebooklm(notebook_type: str = "briefing", **kwargs) -> dict:
    from clawdbot.daemon import handle_notebooklm
    return await handle_notebooklm({"notebook_type": notebook_type, **kwargs})


async def _n8n_workflow(webhook_path: str = "", workflow_payload: dict | None = None, **_) -> dict:
    from clawdbot.daemon import handle_n8n_workflow
    return await handle_n8n_workflow({"webhook_path": webhook_path, "workflow_payload": workflow_payload or {}})


async def _safety_vet(skill_name: str = "", **_) -> dict:
    path = find_skill(skill_name)
    if not path:
        return {"error": f"Skill '{skill_name}' not found"}
    from clawdbot.safety import vet_skill
    result = await vet_skill(path)
    return {"skill": skill_name, "passed": result.passed, "details": result.details[:500]}


async def _capability_list(**_) -> list:
    try:
        from clawdbot.capabilities import CAPABILITY_MAP
        return [{"name": k, "strategies": len(v.get("skills", []))} for k, v in CAPABILITY_MAP.items()]
    except Exception:
        return []


async def _capability_resolve(capability: str = "", context: dict | None = None, **_) -> dict:
    from clawdbot.capability_resolver import resolve_capability
    return await resolve_capability(capability, context or {})


async def _capability_runtime_status(**_) -> dict:
    from shared.db import get_config

    return {
        "runtime_capabilities": await get_config("clawdbot_capability_runtime", {}),
        "agent_mesh": await get_config("clawdbot_agent_mesh", []),
    }


async def _operator_message(message: str = "", **_) -> dict:
    from clawdbot.daemon import handle_operator_message
    return await handle_operator_message({"message": message})


async def _recommend(target: str = "", topic: str = "", message: str = "", **_) -> dict:
    from shared.comms import record_decision
    await record_decision(
        agent="clawdbot",
        decision_type="recommendation",
        context={"target": target, "topic": topic},
        decision={"message": message},
        reasoning=message,
    )
    return {"recommended": True, "target": target, "topic": topic}


async def _infra_health(**_) -> dict:
    """Run ClawdBot's infrastructure health checks."""
    results = {}
    import httpx
    from shared.config import config

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{config.ollama.host}/api/tags")
            results["ollama"] = {"status": "ok" if resp.status_code == 200 else f"http_{resp.status_code}"}
    except Exception as e:
        results["ollama"] = {"status": "down", "error": str(e)[:100]}

    # Mem0
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{config.memory.mem0_host}/v1/memories/search/",
                json={"query": "health", "user_id": "titan", "limit": 1})
            results["mem0"] = {"status": "ok" if resp.status_code < 500 else f"http_{resp.status_code}"}
    except Exception as e:
        results["mem0"] = {"status": "down", "error": str(e)[:100]}

    # Firecrawl
    try:
        from tools.firecrawl_client import get_firecrawl_status
        fc = get_firecrawl_status()
        results["firecrawl"] = {"status": "ok" if fc.get("mode") == "live" else "degraded"}
    except Exception as e:
        results["firecrawl"] = {"status": "unknown", "error": str(e)[:100]}

    return results


async def _voice_call(to: str = "", **kwargs) -> dict:
    from clawdbot.daemon import handle_voice_call
    return await handle_voice_call({"to": to, **kwargs})


async def _whatsapp_message(message: str = "", to: str = "", **kwargs) -> dict:
    from clawdbot.daemon import handle_whatsapp_message
    return await handle_whatsapp_message({"message": message, "to": to, **kwargs})


async def _health_check(**_) -> dict:
    skills = list_installed_skills()
    return {"status": "running", "agent": "clawdbot", "skills_count": len(skills)}


async def _ask(question: str = "", from_agent: str = "", context: dict = None, **_) -> dict:
    """Handle a question from another agent about skills/infra/research."""
    from shared.skill_loader import list_installed_skills
    from shared.db import fetch_val

    skills = list_installed_skills()
    skill_names = [s["name"] for s in skills[:20]] if skills else []

    # Check infra health
    infra = await fetch_val(
        "SELECT value FROM system_config WHERE key = 'infra_health'"
    ) or "unknown"

    from shared.llm_client import llm
    prompt = (
        f"You are ClawdBot, the skills executor. {from_agent} is asking:\n\n"
        f"{question}\n\n"
        f"Available skills: {', '.join(skill_names)}\n"
        f"Infrastructure health: {infra}\n\n"
        f"Answer concisely about your capabilities."
    )

    answer = await llm.generate(prompt, model="fast", max_tokens=300)
    return {"answer": answer, "from": "clawdbot"}


async def _events_recent(limit: int = 20, **_) -> list:
    """Return recent events for this agent."""
    rows = await db.fetch_all(
        "SELECT event_type, payload, created_at FROM events ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    return [dict(r) for r in rows]


async def _event_relay(type: str = "", payload: dict = None, source: str = "", **_) -> dict:
    """Accept a relayed event from OpenJarvis and store it."""
    if not type:
        return {"error": "event type is required"}
    await db.emit_event(f"relayed_{type}", {
        "source": source,
        "original_type": type,
        **(payload or {}),
    })
    return {"status": "relayed", "type": type, "source": source}


CAPABILITY_HANDLERS = {
    "ask": _ask,
    "skill_execute": _skill_execute,
    "skill_list": _skill_list,
    "skill_find": _skill_find,
    "web_scrape": _web_scrape,
    "scrape_company": _scrape_company,
    "browser_task": _browser_task,
    "agent_orchestration": _agent_orchestration,
    "android_automation": _android_automation,
    "site_verify": _site_verify,
    "site_verify_batch": _site_verify_batch,
    "verify_demo_site": _verify_demo_site,
    "enrich_lead": _enrich_lead,
    "enrich_leads_batch": _enrich_leads_batch,
    "voice_call": _voice_call,
    "whatsapp_message": _whatsapp_message,
    "image_generation": _image_generation,
    "notebooklm": _notebooklm,
    "n8n_workflow": _n8n_workflow,
    "safety_vet": _safety_vet,
    "capability_list": _capability_list,
    "capability_resolve": _capability_resolve,
    "capability_runtime_status": _capability_runtime_status,
    "operator_message": _operator_message,
    "recommend": _recommend,
    "infra_health": _infra_health,
    "health_check": _health_check,
    "events_recent": _events_recent,
    "event_relay": _event_relay,
}


async def handle_a2a(input_text: str) -> str:
    """Route A2A requests to ClawdBot capabilities."""
    text = input_text.strip()

    # JSON structured input
    try:
        req = json.loads(text)
        if isinstance(req, dict) and "capability" in req:
            cap = req["capability"]
            params = req.get("params", {})
            handler = CAPABILITY_HANDLERS.get(cap)
            if handler:
                result = await handler(**params)
                return json.dumps(result, indent=2, default=str)
            return json.dumps({"error": f"Unknown capability: {cap}"})
    except (json.JSONDecodeError, TypeError):
        pass

    # Natural language routing
    lower = text.lower()
    if "skill" in lower and ("list" in lower or "available" in lower):
        result = await _skill_list()
    elif "scrape" in lower:
        # Extract URL
        import re
        urls = re.findall(r'https?://\S+', text)
        url = urls[0] if urls else ""
        result = await _web_scrape(url=url)
    elif "android" in lower or "adb" in lower:
        result = await _android_automation()
    elif "voice" in lower or "call" in lower:
        result = {"error": "Specify a structured voice_call request with a target number."}
    elif "agent" in lower and ("mesh" in lower or "orchestrat" in lower):
        result = await _agent_orchestration()
    elif "verify" in lower and "site" in lower:
        import re
        urls = re.findall(r'https?://\S+', text)
        url = urls[0] if urls else ""
        result = await _site_verify(url=url)
    elif "enrich" in lower:
        result = await _enrich_leads_batch()
    elif "image" in lower or "logo" in lower or "graphic" in lower:
        result = await _image_generation()
    elif "capability" in lower and "list" in lower:
        result = await _capability_list()
    elif "infra" in lower or "infrastructure" in lower:
        result = await _infra_health()
    elif "health" in lower:
        result = await _health_check()
    else:
        result = {
            "error": "ClawdBot couldn't route this request.",
            "available_capabilities": list(CAPABILITY_HANDLERS.keys()),
        }

    return json.dumps(result, indent=2, default=str)


def create_clawdbot_a2a(clawdbot_daemon=None) -> "FastAPI":
    health_fn = clawdbot_daemon.health_check if clawdbot_daemon else None
    return create_a2a_app(agent_card=CLAWDBOT_CARD, handler=handle_a2a, health_check=health_fn)
