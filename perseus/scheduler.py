"""
Perseus scheduler — defines what runs when.
AI-managed scheduling: Perseus reads pipeline state and decides priorities.
Fixed intervals are ceilings, not mandates.
"""

from dataclasses import dataclass


@dataclass
class Schedule:
    """A scheduled task with flexible timing bounds."""
    name: str
    interval_seconds: int
    description: str
    # If True, Perseus can skip this when the pipeline state doesn't need it.
    skippable: bool = True
    # Revenue stage this task serves (for priority scoring).
    pipeline_stage: str = ""


# Schedules — interval_seconds is the maximum gap between runs.
# Perseus may run high-priority tasks sooner or skip low-priority ones.
SCHEDULES = [
    # Revenue pipeline
    Schedule("lead_discovery", 1800, "Discover new leads", skippable=True, pipeline_stage="top_of_funnel"),
    Schedule("lead_research", 600, "Research discovered leads", skippable=True, pipeline_stage="top_of_funnel"),
    Schedule("email_compose", 300, "Compose emails", skippable=True, pipeline_stage="outreach"),
    Schedule("email_send", 600, "Send emails", skippable=True, pipeline_stage="outreach"),
    Schedule("follow_up_check", 3600, "Check replies and send follow-ups", skippable=True, pipeline_stage="outreach"),
    Schedule("sync_analytics", 1800, "Sync Instantly.ai campaign analytics", skippable=True, pipeline_stage="outreach"),
    Schedule("close_interested", 1800, "Process interested leads", skippable=True, pipeline_stage="closing"),
    Schedule("build_sites", 3600, "Build sites for closed deals", skippable=True, pipeline_stage="delivery"),
    Schedule("process_invoices", 7200, "Process invoices", skippable=True, pipeline_stage="delivery"),
    # Learning & strategy
    Schedule("daily_reflection", 86400, "Daily learning reflection", skippable=False),
    Schedule("weekly_strategy", 604800, "Weekly strategy review", skippable=False),
    Schedule("revenue_expansion_review", 86400, "Review expansion ideas", skippable=False),
    # Infrastructure (never skip)
    Schedule("health_check", 300, "Health check", skippable=False),
    Schedule("budget_check", 3600, "Budget enforcement", skippable=False),
    Schedule("deliverability_check", 1800, "Monitor deliverability and domain health", skippable=False),
    Schedule("sleep_cycle", 86400, "Nightly contrarian review — Opus debates system changes", skippable=False),
    Schedule("morning_briefing", 86400, "Morning briefing to Nico", skippable=False),
    # Memory maintenance (DeerFlow Phase 3)
    Schedule("memory_cleanup", 86400, "Clean up expired daemon memories", skippable=False),
    # ClawdBot tasks
    Schedule("site_verify", 3600, "Verify deployed sites are live", skippable=True, pipeline_stage="delivery"),
    Schedule("enrich_leads", 1800, "Enrich leads missing data", skippable=True, pipeline_stage="top_of_funnel"),
]

SCHEDULE_MAP = {s.name: s for s in SCHEDULES}
