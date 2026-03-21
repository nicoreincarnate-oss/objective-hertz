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
import signal
import logging
import json

from shared.config import config
from shared.logging_config import setup_logging
from shared import db
from shared.agent_base import AgentBase
from shared.skill_loader import find_skill, execute_skill, list_installed_skills

from perseus.agent_registry import heartbeat

logger = setup_logging("clawdbot")


class ClawdBotDaemon(AgentBase):
    name = "clawdbot"
    description = "Skills executor, browser automator, web scraper, and site verifier."

    def __init__(self):
        super().__init__()
        self._running = False
        self._cycle_interval = 15  # Check for tasks every 15 seconds

    async def start(self):
        """Start ClawdBot's main loop."""
        logger.info("ClawdBot starting up...")
        await db.init_pool()
        await self.register()
        self._running = True

        # Log available skills
        skills = list_installed_skills()
        logger.info(f"ClawdBot is LIVE. {len(skills)} skills available: {[s['name'] for s in skills]}")

        while self._running:
            try:
                paused = await db.get_config("clawdbot_paused", False)
                if paused:
                    logger.debug("ClawdBot is paused. Waiting.")
                    await asyncio.sleep(10)
                    continue

                await self._process_task_queue()
                await heartbeat(self.name)

            except Exception as e:
                logger.error(f"ClawdBot cycle error: {e}", exc_info=True)
                await self.emit_event("clawdbot_error", {"error": str(e)})

            await asyncio.sleep(self._cycle_interval)

    async def stop(self):
        """Gracefully stop ClawdBot."""
        logger.info("ClawdBot shutting down...")
        self._running = False
        await self.deregister()
        await db.close_pool()
        logger.info("ClawdBot stopped.")

    async def health_check(self) -> dict:
        skills = list_installed_skills()
        return {
            "agent": self.name,
            "status": "running" if self._running else "stopped",
            "skills_available": len(skills),
        }

    async def _process_task_queue(self):
        """Process pending tasks assigned to ClawdBot."""
        tasks = await self.get_pending_tasks()
        for task in tasks:
            task_type = task["task_type"]
            handler = TASK_HANDLERS.get(task_type)
            if not handler:
                continue

            claimed = await self.claim_task(task["id"])
            if not claimed:
                continue

            try:
                payload = task.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                await handler(payload)
                await self.complete_task(task["id"])
                logger.debug(f"Task {task['id']} ({task_type}) completed")
            except Exception as e:
                await self.fail_task(task["id"], str(e))
                logger.error(f"Task {task['id']} ({task_type}) failed: {e}")


# ── Task Handlers ──────────────────────────────────────────────────

async def handle_skill_execute(payload: dict):
    """Execute a named skill with given context."""
    skill_name = payload.get("skill_name", "")
    task_prompt = payload.get("prompt", "")
    context = payload.get("context", {})

    if not skill_name:
        raise ValueError("skill_name is required")

    skill_path = find_skill(skill_name)
    if not skill_path:
        raise ValueError(f"Skill '{skill_name}' not found")

    result = await execute_skill(skill_name, task_prompt, context)

    # Store result as event so requesting daemon can pick it up
    await db.emit_event("skill_result", {
        "skill": skill_name,
        "result": result[:2000] if result else "",
        "request_id": payload.get("request_id", ""),
    })

    # Store as learning if tagged
    if payload.get("store_learning"):
        await _store_learning("skill_execution", f"Skill {skill_name}: {result[:500]}")

    logger.info(f"Skill '{skill_name}' executed successfully")


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


async def handle_site_verify(payload: dict):
    """Verify a deployed site is live and functional."""
    url = payload.get("url", "")
    client_id = payload.get("client_id")

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
                })

            logger.info(f"Site verify: {url} → {status_code} ({'LIVE' if is_live else 'DOWN'})")

    except Exception as e:
        await db.emit_event("site_down", {
            "url": url,
            "error": str(e),
            "client_id": client_id,
        })
        raise


async def handle_browser_task(payload: dict):
    """Execute a browser automation task (future: browser-use integration)."""
    task_description = payload.get("description", "")
    url = payload.get("url", "")

    # For now, delegate to skill system or firecrawl
    # browser-use integration would go here when wired
    skill_path = find_skill("browser-automation")
    if skill_path:
        result = await execute_skill("browser-automation", task_description, {"url": url})
        await db.emit_event("browser_result", {
            "result": result[:2000] if result else "",
            "request_id": payload.get("request_id", ""),
        })
    else:
        # Fallback: just scrape the URL
        if url:
            await handle_web_scrape(payload)
        else:
            raise ValueError("No browser-automation skill and no URL provided")


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
    })


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


TASK_HANDLERS = {
    "skill_execute": handle_skill_execute,
    "web_scrape": handle_web_scrape,
    "verify_single_site": handle_site_verify,
    "browser_task": handle_browser_task,
    "enrich_lead": handle_enrich_lead,
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


if __name__ == "__main__":
    asyncio.run(main())
