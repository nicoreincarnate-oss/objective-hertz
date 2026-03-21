"""
Perseus scheduler — defines what runs when.
AI-managed scheduling with reasonable defaults.
"""

from dataclasses import dataclass


@dataclass
class Schedule:
    """A scheduled task."""
    name: str
    interval_seconds: int
    description: str


# Default schedule — Perseus can adjust these based on learnings
SCHEDULES = [
    Schedule("lead_discovery", 1800, "Discover new leads every 30 minutes"),
    Schedule("lead_research", 600, "Research discovered leads every 10 minutes"),
    Schedule("email_compose", 300, "Compose emails every 5 minutes"),
    Schedule("email_send", 600, "Send emails every 10 minutes"),
    Schedule("follow_up_check", 3600, "Check replies and send follow-ups hourly"),
    Schedule("sync_analytics", 1800, "Sync Instantly.ai campaign analytics every 30 min"),
    Schedule("close_interested", 1800, "Process interested leads every 30 minutes"),
    Schedule("build_sites", 3600, "Build sites for closed deals hourly"),
    Schedule("process_invoices", 7200, "Process invoices every 2 hours"),
    Schedule("daily_reflection", 86400, "Daily learning reflection at end of day"),
    Schedule("weekly_strategy", 604800, "Weekly strategy review"),
    Schedule("revenue_expansion_review", 86400, "Review ROI-positive expansion ideas daily"),
    Schedule("health_check", 300, "Health check every 5 minutes"),
    Schedule("budget_check", 3600, "Budget enforcement hourly"),
    Schedule("morning_briefing", 86400, "Morning briefing to Nico at 7 AM"),
    # ClawdBot tasks
    Schedule("site_verify", 3600, "Verify deployed sites are live hourly"),
    Schedule("enrich_leads", 1800, "Enrich leads missing email/data every 30 min"),
]
