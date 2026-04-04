"""Register all Perseus tools in the UnifiedToolRegistry.

Called during daemon startup to make all tools discoverable via MCP.
Registers: Python clients, SKILL.md files, OJ tools, daemon handlers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("perseus.register_tools")


async def register_all_tools() -> int:
    """Register all available tools. Called at startup.

    Returns the total number of tools registered.
    """
    from shared.tool_registry import get_registry

    registry = get_registry()
    count = 0

    # 1. Register Python tool clients
    count += _register_apify_tools(registry)
    count += _register_tavily_tools(registry)
    count += _register_twentyfirst_tools(registry)
    count += _register_firecrawl_tools(registry)
    count += _register_instantly_tools(registry)
    count += _register_n8n_tools(registry)
    count += _register_recraft_tools(registry)
    count += _register_claude_code_tools(registry)
    count += _register_sandbox_tools(registry)
    count += _register_visual_qa_tools(registry)
    count += _register_browser_use_tools(registry)

    # 2. Auto-scan SKILL.md files
    count += await _register_skills(registry)

    # 3. Register OJ tools (from openjarvis/tools/)
    count += _register_oj_tools(registry)

    logger.info("Registered %d tools in ToolRegistry", count)
    return count


# ---------------------------------------------------------------------------
# Individual tool registration helpers
# ---------------------------------------------------------------------------


def _register_apify_tools(registry: Any) -> int:
    """Register Apify lead discovery and enrichment tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.apify_client import discover_leads, enrich_lead_emails

        registry.register(HandlerToolAdapter(
            handler_fn=discover_leads,
            name="apify_discover_leads",
            description="Discover business leads via Apify Google Maps scraper. Returns name, phone, email, website, address, rating, reviews.",
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Business type or search query"},
                    "location": {"type": "string", "description": "City/region to search"},
                    "max_results": {"type": "integer", "default": 50},
                    "actor_id": {"type": "string", "default": "compass/crawler-google-places"},
                },
                "required": ["query", "location"],
            },
            category="lead_discovery",
            cost_estimate=0.05,
            tags=["apify", "leads", "discovery", "google_maps", "scraping"],
            requires_env=["APIFY_API_TOKEN"],
            timeout_seconds=120.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=enrich_lead_emails,
            name="apify_enrich_emails",
            description="Enrich leads with email addresses by crawling their websites via Apify.",
            schema={
                "type": "object",
                "properties": {
                    "leads": {"type": "array", "description": "List of lead dicts with 'website' key"},
                    "actor_id": {"type": "string", "default": "solutionssmart/local-business-lead-finder"},
                },
                "required": ["leads"],
            },
            category="lead_enrichment",
            cost_estimate=0.03,
            tags=["apify", "leads", "enrichment", "email"],
            requires_env=["APIFY_API_TOKEN"],
            timeout_seconds=180.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("apify_client not importable, skipping Apify tools")

    return registered


def _register_tavily_tools(registry: Any) -> int:
    """Register Tavily web search and business research tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.tavily_client import search, research_business

        registry.register(HandlerToolAdapter(
            handler_fn=search,
            name="tavily_search",
            description="AI-optimized web search via Tavily. Returns title, URL, content, score for each result.",
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "search_depth": {"type": "string", "default": "basic", "description": "'basic' (1 credit) or 'advanced' (2 credits)"},
                    "topic": {"type": "string", "default": "general"},
                    "max_results": {"type": "integer", "default": 5},
                    "include_domains": {"type": "array", "description": "Only search these domains"},
                    "exclude_domains": {"type": "array", "description": "Exclude these domains"},
                },
                "required": ["query"],
            },
            category="search",
            cost_estimate=0.01,
            tags=["tavily", "search", "web", "research"],
            requires_env=["TAVILY_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=research_business,
            name="tavily_research_business",
            description="Research a business for lead enrichment: website quality, reviews, competitors, social media.",
            schema={
                "type": "object",
                "properties": {
                    "business_name": {"type": "string", "description": "Name of the business"},
                    "location": {"type": "string", "default": ""},
                    "aspects": {"type": "array", "description": "What to research"},
                },
                "required": ["business_name"],
            },
            category="lead_enrichment",
            cost_estimate=0.03,
            tags=["tavily", "research", "enrichment", "business"],
            requires_env=["TAVILY_API_KEY"],
            timeout_seconds=60.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("tavily_client not importable, skipping Tavily tools")

    return registered


def _register_twentyfirst_tools(registry: Any) -> int:
    """Register 21st.dev MCP component generation tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.twentyfirst_dev import generate_component, generate_site_sections

        registry.register(HandlerToolAdapter(
            handler_fn=generate_component,
            name="twentyfirst_generate_component",
            description="Generate a production-ready React/TypeScript component from a natural language description via 21st.dev MCP.",
            schema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What the component should look like/do"},
                    "search_query": {"type": "string", "description": "2-4 word search query for component inspiration"},
                },
                "required": ["description", "search_query"],
            },
            category="code_generation",
            cost_estimate=0.02,
            tags=["21st_dev", "react", "component", "ui", "generation", "site_building"],
            requires_env=["TWENTYFIRST_API_KEY"],
            timeout_seconds=120.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=generate_site_sections,
            name="twentyfirst_generate_site_sections",
            description="Generate multiple website sections in batch via 21st.dev MCP.",
            schema={
                "type": "object",
                "properties": {
                    "sections": {"type": "array", "description": "List of dicts with 'name', 'description', 'search_query'"},
                    "project_dir": {"type": "string", "default": "/tmp/perseus-site"},
                },
                "required": ["sections"],
            },
            category="code_generation",
            cost_estimate=0.10,
            tags=["21st_dev", "react", "site", "generation", "batch", "site_building"],
            requires_env=["TWENTYFIRST_API_KEY"],
            timeout_seconds=300.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("twentyfirst_dev not importable, skipping 21st.dev tools")

    return registered


def _register_firecrawl_tools(registry: Any) -> int:
    """Register Firecrawl web scraping and search tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.firecrawl_client import scrape_url, search_web, enrich_business_profile

        registry.register(HandlerToolAdapter(
            handler_fn=scrape_url,
            name="firecrawl_scrape",
            description="Scrape a web page via Firecrawl API. Returns markdown content, metadata.",
            schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to scrape"},
                    "formats": {"type": "array", "default": ["markdown"]},
                    "only_main_content": {"type": "boolean", "default": True},
                    "timeout": {"type": "integer", "default": 20},
                },
                "required": ["url"],
            },
            category="scraping",
            cost_estimate=0.01,
            tags=["firecrawl", "scrape", "web", "extraction"],
            requires_env=["FIRECRAWL_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=search_web,
            name="firecrawl_search",
            description="Web search via Firecrawl hosted API. Returns search results with title, URL, description.",
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 5},
                    "timeout": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
            category="search",
            cost_estimate=0.01,
            tags=["firecrawl", "search", "web"],
            requires_env=["FIRECRAWL_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=enrich_business_profile,
            name="firecrawl_enrich_business",
            description="Combine Firecrawl search and scrape into compact lead research for a business.",
            schema={
                "type": "object",
                "properties": {
                    "business_name": {"type": "string", "description": "Name of the business to research"},
                    "city": {"type": "string", "default": ""},
                    "industry": {"type": "string", "default": ""},
                    "website_url": {"type": "string", "default": ""},
                    "search_query": {"type": "string", "default": ""},
                    "search_limit": {"type": "integer", "default": 3},
                },
                "required": ["business_name"],
            },
            category="lead_enrichment",
            cost_estimate=0.02,
            tags=["firecrawl", "enrichment", "research", "business"],
            requires_env=["FIRECRAWL_API_KEY"],
            timeout_seconds=45.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("firecrawl_client not importable, skipping Firecrawl tools")

    return registered


def _register_instantly_tools(registry: Any) -> int:
    """Register Instantly.ai cold email campaign tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.instantly_client import InstantlyClient

        # Wrap class methods as standalone async functions
        async def instantly_create_campaign(name: str) -> dict:
            client = InstantlyClient()
            try:
                return await client.create_campaign(name)
            finally:
                await client.close()

        async def instantly_add_leads_bulk(campaign_id: str, leads: list[dict]) -> dict:
            client = InstantlyClient()
            try:
                return await client.add_leads_bulk(campaign_id, leads)
            finally:
                await client.close()

        async def instantly_activate_campaign(campaign_id: str) -> dict:
            client = InstantlyClient()
            try:
                return await client.activate_campaign(campaign_id)
            finally:
                await client.close()

        async def instantly_get_analytics(campaign_id: str = "") -> dict:
            client = InstantlyClient()
            try:
                return await client.get_campaign_analytics(campaign_id)
            finally:
                await client.close()

        async def instantly_list_emails(campaign_id: str = "", is_unread: bool | None = None) -> Any:
            client = InstantlyClient()
            try:
                return await client.list_emails(campaign_id, is_unread)
            finally:
                await client.close()

        registry.register(HandlerToolAdapter(
            handler_fn=instantly_create_campaign,
            name="instantly_create_campaign",
            description="Create a new Instantly.ai email campaign with auto-sequences disabled (Perseus owns sequencing).",
            schema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Campaign name"}},
                "required": ["name"],
            },
            category="email",
            cost_estimate=0.0,
            tags=["instantly", "email", "campaign", "outreach"],
            requires_env=["INSTANTLY_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=instantly_add_leads_bulk,
            name="instantly_add_leads_bulk",
            description="Add up to 1000 leads to an Instantly campaign with custom variables for personalization.",
            schema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string"},
                    "leads": {"type": "array", "description": "Lead dicts with 'email' and custom variables"},
                },
                "required": ["campaign_id", "leads"],
            },
            category="email",
            cost_estimate=0.0,
            tags=["instantly", "email", "leads", "bulk"],
            requires_env=["INSTANTLY_API_KEY"],
            timeout_seconds=60.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=instantly_activate_campaign,
            name="instantly_activate_campaign",
            description="Activate an Instantly campaign to start sending emails.",
            schema={
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}},
                "required": ["campaign_id"],
            },
            category="email",
            cost_estimate=0.0,
            tags=["instantly", "email", "campaign", "activate"],
            requires_env=["INSTANTLY_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=instantly_get_analytics,
            name="instantly_get_analytics",
            description="Get Instantly campaign analytics: sent, opens, clicks, replies.",
            schema={
                "type": "object",
                "properties": {"campaign_id": {"type": "string", "default": ""}},
            },
            category="analytics",
            cost_estimate=0.0,
            tags=["instantly", "email", "analytics"],
            requires_env=["INSTANTLY_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=instantly_list_emails,
            name="instantly_list_emails",
            description="List emails (replies, sent) from Instantly inbox. Use to check for new replies.",
            schema={
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "default": ""},
                    "is_unread": {"type": "boolean"},
                },
            },
            category="email",
            cost_estimate=0.0,
            tags=["instantly", "email", "inbox", "replies"],
            requires_env=["INSTANTLY_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("instantly_client not importable, skipping Instantly tools")

    return registered


def _register_n8n_tools(registry: Any) -> int:
    """Register N8N workflow automation tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.n8n_client import trigger_workflow, list_workflows

        registry.register(HandlerToolAdapter(
            handler_fn=trigger_workflow,
            name="n8n_trigger_workflow",
            description="Trigger an N8N workflow via webhook and return the result.",
            schema={
                "type": "object",
                "properties": {
                    "webhook_path": {"type": "string", "description": "Webhook path (e.g. /webhook/lead-enrichment)"},
                    "payload": {"type": "object", "description": "JSON body to send to the workflow"},
                    "timeout": {"type": "number", "default": 60.0},
                },
                "required": ["webhook_path", "payload"],
            },
            category="automation",
            cost_estimate=0.0,
            tags=["n8n", "workflow", "automation", "webhook"],
            timeout_seconds=120.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=list_workflows,
            name="n8n_list_workflows",
            description="List active N8N workflows.",
            schema={"type": "object", "properties": {}},
            category="automation",
            cost_estimate=0.0,
            tags=["n8n", "workflow", "list"],
            requires_env=["N8N_USER", "N8N_PASSWORD"],
            timeout_seconds=15.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("n8n_client not importable, skipping N8N tools")

    return registered


def _register_recraft_tools(registry: Any) -> int:
    """Register Recraft AI image generation tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.recraft_client import (
            generate_image,
            generate_logo,
            generate_hero_image,
            generate_social_graphic,
            remove_background,
        )

        registry.register(HandlerToolAdapter(
            handler_fn=generate_image,
            name="recraft_generate_image",
            description="Generate an image from a text prompt via Recraft AI.",
            schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What to generate"},
                    "style": {"type": "string", "default": "realistic_image", "description": "realistic_image, digital_illustration, vector_illustration, icon"},
                    "width": {"type": "integer", "default": 1024},
                    "height": {"type": "integer", "default": 1024},
                    "model": {"type": "string", "default": "recraftv3"},
                },
                "required": ["prompt"],
            },
            category="image_generation",
            cost_estimate=0.01,
            tags=["recraft", "image", "generation", "ai"],
            requires_env=["RECRAFT_API_KEY"],
            timeout_seconds=60.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=generate_logo,
            name="recraft_generate_logo",
            description="Generate a professional logo for a client website via Recraft.",
            schema={
                "type": "object",
                "properties": {
                    "business_name": {"type": "string"},
                    "industry": {"type": "string", "default": ""},
                    "style": {"type": "string", "default": "vector_illustration"},
                },
                "required": ["business_name"],
            },
            category="image_generation",
            cost_estimate=0.01,
            tags=["recraft", "logo", "branding", "site_building"],
            requires_env=["RECRAFT_API_KEY"],
            timeout_seconds=60.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=generate_hero_image,
            name="recraft_generate_hero",
            description="Generate a hero section image for a client website.",
            schema={
                "type": "object",
                "properties": {
                    "business_name": {"type": "string"},
                    "industry": {"type": "string", "default": ""},
                    "city": {"type": "string", "default": ""},
                },
                "required": ["business_name"],
            },
            category="image_generation",
            cost_estimate=0.01,
            tags=["recraft", "hero", "image", "site_building"],
            requires_env=["RECRAFT_API_KEY"],
            timeout_seconds=60.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=generate_social_graphic,
            name="recraft_generate_social",
            description="Generate a social media graphic for a business.",
            schema={
                "type": "object",
                "properties": {
                    "business_name": {"type": "string"},
                    "text": {"type": "string", "description": "Text overlay content"},
                    "style": {"type": "string", "default": "digital_illustration"},
                },
                "required": ["business_name", "text"],
            },
            category="image_generation",
            cost_estimate=0.01,
            tags=["recraft", "social", "graphic", "marketing"],
            requires_env=["RECRAFT_API_KEY"],
            timeout_seconds=60.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=remove_background,
            name="recraft_remove_background",
            description="Remove background from an image via Recraft AI.",
            schema={
                "type": "object",
                "properties": {
                    "image_url": {"type": "string", "description": "URL of the image"},
                    "timeout": {"type": "number", "default": 30.0},
                },
                "required": ["image_url"],
            },
            category="image_generation",
            cost_estimate=0.01,
            tags=["recraft", "image", "background_removal"],
            requires_env=["RECRAFT_API_KEY"],
            timeout_seconds=30.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("recraft_client not importable, skipping Recraft tools")

    return registered


def _register_claude_code_tools(registry: Any) -> int:
    """Register Claude Agent SDK tools for autonomous code generation."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.claude_code_tool import run_agent_task, build_site, generate_code

        registry.register(HandlerToolAdapter(
            handler_fn=run_agent_task,
            name="claude_code_run_task",
            description="Run an autonomous agent task using Claude Agent SDK. Supports tool use, MCP servers, and budget caps.",
            schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What the agent should do"},
                    "cwd": {"type": "string", "default": "/tmp/perseus-workspace"},
                    "model": {"type": "string", "default": "claude-sonnet-4-6"},
                    "max_budget_usd": {"type": "number", "default": 2.0},
                    "max_turns": {"type": "integer", "default": 50},
                },
                "required": ["prompt"],
            },
            category="code_generation",
            cost_estimate=0.50,
            tags=["claude", "agent", "code", "generation", "autonomous"],
            requires_env=["ANTHROPIC_API_KEY"],
            timeout_seconds=600.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=build_site,
            name="claude_code_build_site",
            description="Build a complete website using Claude Agent SDK with optional 21st.dev MCP components.",
            schema={
                "type": "object",
                "properties": {
                    "brief": {"type": "object", "description": "Site brief with business_name, industry, services, tone, pages"},
                    "output_dir": {"type": "string", "default": "/tmp/perseus-site"},
                    "use_21st_dev": {"type": "boolean", "default": True},
                },
                "required": ["brief"],
            },
            category="site_building",
            cost_estimate=1.00,
            tags=["claude", "site_building", "website", "react"],
            requires_env=["ANTHROPIC_API_KEY"],
            timeout_seconds=900.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=generate_code,
            name="claude_code_generate",
            description="General-purpose code generation for fixes, refactoring, or module generation.",
            schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "What code to generate or fix"},
                    "cwd": {"type": "string", "description": "Working directory"},
                    "max_budget_usd": {"type": "number", "default": 1.0},
                },
                "required": ["task", "cwd"],
            },
            category="code_generation",
            cost_estimate=0.30,
            tags=["claude", "code", "generation", "refactor"],
            requires_env=["ANTHROPIC_API_KEY"],
            timeout_seconds=300.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("claude_code_tool not importable, skipping Claude Code tools")

    return registered


def _register_sandbox_tools(registry: Any) -> int:
    """Register sandboxed code execution tools."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.sandbox_executor import execute_code, execute_file, verify_site

        registry.register(HandlerToolAdapter(
            handler_fn=execute_code,
            name="sandbox_execute_code",
            description="Execute code in a sandboxed Docker/Podman container with resource limits.",
            schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Code to execute"},
                    "language": {"type": "string", "default": "python"},
                    "timeout": {"type": "integer", "default": 30},
                    "memory_limit": {"type": "string", "default": "256m"},
                    "cpu_limit": {"type": "number", "default": 0.5},
                },
                "required": ["code"],
            },
            category="execution",
            cost_estimate=0.0,
            tags=["sandbox", "execution", "docker", "code"],
            timeout_seconds=60.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=execute_file,
            name="sandbox_execute_file",
            description="Execute a file in a sandboxed container. Auto-detects language from extension.",
            schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file to execute"},
                    "language": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["file_path"],
            },
            category="execution",
            cost_estimate=0.0,
            tags=["sandbox", "execution", "file"],
            timeout_seconds=60.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=verify_site,
            name="sandbox_verify_site",
            description="Start a simple HTTP server in sandbox and verify a generated site loads correctly.",
            schema={
                "type": "object",
                "properties": {
                    "site_dir": {"type": "string", "description": "Directory containing the site files"},
                    "port": {"type": "integer", "default": 8080},
                    "timeout": {"type": "integer", "default": 10},
                },
                "required": ["site_dir"],
            },
            category="qa",
            cost_estimate=0.0,
            tags=["sandbox", "verification", "site", "qa"],
            timeout_seconds=30.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("sandbox_executor not importable, skipping sandbox tools")

    return registered


def _register_visual_qa_tools(registry: Any) -> int:
    """Register visual QA gate tools for site evaluation."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0
    try:
        from tools.visual_qa_gate import evaluate_site, run_qa_loop

        registry.register(HandlerToolAdapter(
            handler_fn=evaluate_site,
            name="visual_qa_evaluate",
            description="Full visual QA evaluation: screenshots at 3 viewports, Claude vision scoring on 7 design dimensions. Gates deployment.",
            schema={
                "type": "object",
                "properties": {
                    "site_url": {"type": "string", "description": "URL of the site to evaluate"},
                    "attempt": {"type": "integer", "default": 0},
                },
                "required": ["site_url"],
            },
            category="qa",
            cost_estimate=0.05,
            tags=["qa", "visual", "design", "scoring", "site_building"],
            requires_env=["ANTHROPIC_API_KEY"],
            timeout_seconds=120.0,
        ))
        registered += 1

        registry.register(HandlerToolAdapter(
            handler_fn=run_qa_loop,
            name="visual_qa_loop",
            description="Run the full QA loop: evaluate site, regenerate if needed, re-evaluate until pass or escalate.",
            schema={
                "type": "object",
                "properties": {
                    "site_url": {"type": "string", "description": "URL of the site to evaluate"},
                },
                "required": ["site_url"],
            },
            category="qa",
            cost_estimate=0.15,
            tags=["qa", "visual", "loop", "design", "site_building"],
            requires_env=["ANTHROPIC_API_KEY"],
            timeout_seconds=600.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("visual_qa_gate not importable, skipping Visual QA tools")

    return registered


def _register_browser_use_tools(registry: Any) -> int:
    """Register browser-use sidecar tools for browser automation."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0

    # browser-use is used indirectly via visual_qa_gate's capture_screenshots.
    # Register the screenshot capture as a standalone tool.
    try:
        from tools.visual_qa_gate import capture_screenshots

        registry.register(HandlerToolAdapter(
            handler_fn=capture_screenshots,
            name="browser_use_capture_screenshots",
            description="Capture screenshots of a site at desktop, tablet, and mobile viewports via browser-use sidecar.",
            schema={
                "type": "object",
                "properties": {
                    "site_url": {"type": "string", "description": "URL to screenshot"},
                },
                "required": ["site_url"],
            },
            category="browser",
            cost_estimate=0.0,
            tags=["browser", "screenshot", "viewport", "qa"],
            timeout_seconds=90.0,
        ))
        registered += 1

    except ImportError:
        logger.debug("browser-use tools not importable, skipping")

    return registered


async def _register_skills(registry: Any) -> int:
    """Auto-scan and register SKILL.md files from known directories."""
    from shared.tool_registry import SkillToolAdapter

    registered = 0
    project_root = Path(__file__).parent.parent

    skill_dirs = [
        project_root / "hermes" / "skills",
        project_root / ".agent" / "skills",
        Path.home() / ".openclaw" / "skills",
        Path.home() / ".hermes" / "skills",
    ]

    for skill_dir in skill_dirs:
        if not skill_dir.exists():
            continue
        for skill_md in skill_dir.rglob("SKILL.md"):
            skill_name = skill_md.parent.name
            try:
                adapter = SkillToolAdapter(skill_md, skill_name)
                registry.register(adapter)
                registered += 1
            except Exception as exc:
                logger.warning("Failed to register skill '%s': %s", skill_name, exc)

    return registered


def _register_oj_tools(registry: Any) -> int:
    """Register OpenJarvis native tools from the tools directory."""
    from shared.tool_registry import HandlerToolAdapter

    registered = 0

    # The OJ tools are registered via auto_scan_all's _scan_oj_tools phase.
    # Here we register the OJ tools that Perseus wraps with specific interfaces.
    oj_tool_modules = [
        ("openjarvis.tools.web_search", "WebSearchTool", "web_search", "search", ["search", "web"]),
        ("openjarvis.tools.browser_axtree", "BrowserAXTreeTool", "browser_axtree", "browser", ["browser", "accessibility"]),
        ("openjarvis.tools.code_interpreter", "CodeInterpreterTool", "code_interpreter", "execution", ["code", "interpreter"]),
        ("openjarvis.tools.image_tool", "ImageTool", "image_tool", "image", ["image", "vision"]),
        ("openjarvis.tools.audio_tool", "AudioTool", "audio_tool", "audio", ["audio", "transcription"]),
        ("openjarvis.tools.memory_manage", "MemoryManageTool", "memory_manage", "memory", ["memory", "knowledge"]),
        ("openjarvis.tools.storage_tools", "StorageTool", "storage_tool", "storage", ["storage", "retrieval"]),
        ("openjarvis.tools.channel_tools", "ChannelTool", "channel_tool", "communication", ["channel", "messaging"]),
    ]

    for mod_name, cls_name, tool_name, category, tags in oj_tool_modules:
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue

            instance = cls()
            if hasattr(instance, "spec") and hasattr(instance, "execute"):
                oj_spec = instance.spec
                adapter = HandlerToolAdapter(
                    handler_fn=instance.execute,
                    name=f"oj_{tool_name}",
                    schema=getattr(oj_spec, "parameters", {}),
                    description=getattr(oj_spec, "description", tool_name),
                    category=category,
                    cost_estimate=getattr(oj_spec, "cost_estimate", 0.0),
                    source=f"oj:{mod_name}",
                    tags=["oj", "native"] + tags,
                    timeout_seconds=getattr(oj_spec, "timeout_seconds", 30.0),
                )
                registry.register(adapter)
                registered += 1

        except (ImportError, AttributeError, Exception) as exc:
            logger.debug("OJ tool '%s' not available: %s", tool_name, exc)

    return registered


__all__ = ["register_all_tools"]
