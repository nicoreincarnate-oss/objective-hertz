"""Task routing — maps task types to agent names for A2A dispatch.

When a task is requested via comms.request_task(), this map determines
which agent handles it. If not found here, falls back to DB task_queue.
"""

# Task type → agent name (matches keys in oj_bridge.AGENT_URLS)
TASK_ROUTING: dict[str, str] = {
    # Titan — revenue pipeline
    "lead_discovery": "titan",
    "lead_research": "titan",
    "email_compose": "titan",
    "email_send": "titan",
    "follow_up_check": "titan",
    "close_interested": "titan",
    "build_sites": "titan",
    "process_invoices": "titan",
    "sync_analytics": "titan",
    "deliverability_check": "titan",
    "daily_reflection": "titan",
    "weekly_strategy": "titan",
    "revenue_expansion_review": "titan",
    "budget_check": "titan",

    # Hermes — communications
    "morning_briefing": "hermes",
    "send_telegram": "hermes",
    "send_alert": "hermes",

    # ClawdBot — execution
    "web_scrape": "clawdbot",
    "skill_execute": "clawdbot",
    "enrich_lead": "clawdbot",
    "enrich_leads": "clawdbot",
    "browser_task": "clawdbot",
    "site_verify": "clawdbot",
    "site_verify_batch": "clawdbot",
    "verify_demo_site": "clawdbot",
    "handle_n8n_workflow": "clawdbot",
    "image_generation": "clawdbot",

    # Ruflo — engineering swarm
    "code_fix": "ruflo",
    "code_review": "ruflo",
    "code_refactor": "ruflo",
    "security_scan": "ruflo",
    "dependency_audit": "ruflo",
    "implement_tool": "ruflo",
    "test_generate": "ruflo",
}
