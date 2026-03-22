"""Presentation helpers for the Hermes operator dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from hermes.web.operator_chat import build_operator_thread, get_agent_options


PIPELINE_ORDER = [
    "discovered",
    "researched",
    "email_sent",
    "followed_up",
    "replied",
    "interested",
    "demo_built",
    "proposal_sent",
    "closed",
    "building",
    "deployed",
    "invoiced",
    "paid",
]


def build_dashboard_view_model(
    *,
    pipeline_rows: Sequence[Mapping[str, Any]],
    total_revenue: float,
    pending_revenue: float,
    emails_today: int,
    emails_week: int,
    total_leads: int,
    interested: int,
    closed: int,
    pending_review: int,
    review_mode: bool,
    sales_completed: int,
    events: Sequence[Mapping[str, Any]],
    operator_status: str = "",
    operator_error: str = "",
) -> dict[str, Any]:
    pipeline = {
        str(row.get("status", "unknown")): int(row.get("count", 0) or 0)
        for row in pipeline_rows
    }
    latest_event = events[0] if events else None
    latest_event_summary = _event_payload_summary(latest_event) if latest_event else "No fresh movement yet."

    return {
        "mode_label": "Review Mode" if review_mode else "Autonomous",
        "mode_tone": "warning" if review_mode else "success",
        "hero_summary": _build_hero_summary(
            pending_review=pending_review,
            pending_revenue=pending_revenue,
            review_mode=review_mode,
            latest_event=latest_event,
        ),
        "hero_detail": (
            f"{emails_today} emails moved today, {interested} warm leads are active, "
            f"and {sales_completed} sales have been landed so far."
        ),
        "hero_context": latest_event_summary,
        "signal_rail": [
            {"label": "Mode", "value": "Guided" if review_mode else "Live", "tone": "warning" if review_mode else "success"},
            {"label": "Review", "value": f"{pending_review} waiting", "tone": "warning" if pending_review else "success"},
            {"label": "Closed", "value": str(closed), "tone": "default"},
            {"label": "Synced", "value": _format_sync_time(latest_event), "tone": "default"},
        ],
        "metrics": [
            {
                "label": "Revenue Cleared",
                "value": _format_currency(total_revenue),
                "detail": f"{sales_completed} paid or delivered wins",
                "tone": "success",
            },
            {
                "label": "Pending Revenue",
                "value": _format_currency(pending_revenue),
                "detail": "Deals that need collection or decision",
                "tone": "warning" if pending_revenue else "default",
            },
            {
                "label": "Emails Today",
                "value": f"{emails_today:,}",
                "detail": f"{emails_week:,} sent in the last 7 days",
                "tone": "default",
            },
            {
                "label": "Close Rate",
                "value": f"{_close_rate(total_leads, closed):.1f}%",
                "detail": f"{closed} closed from {total_leads:,} tracked leads",
                "tone": "success" if closed else "default",
            },
        ],
        "priority_queue": _build_priority_queue(
            pending_review=pending_review,
            pending_revenue=pending_revenue,
            emails_today=emails_today,
            emails_week=emails_week,
            total_leads=total_leads,
            interested=interested,
            closed=closed,
            review_mode=review_mode,
        ),
        "pipeline_stages": _build_pipeline_stages(pipeline),
        "pipeline_total": sum(pipeline.values()),
        "signal_ledger": [_build_event_card(event) for event in events[:12]],
        "empty_signal_ledger": "No events yet. Titan is warming up.",
        "agent_options": get_agent_options(),
        "operator_thread": build_operator_thread(events),
        "operator_empty_thread": "No agent messages yet. Queue a note to start the thread.",
        "operator_status": operator_status,
        "operator_error": operator_error,
        "footer_note": "Auto-refreshes every 30 seconds.",
    }


def _build_hero_summary(
    *,
    pending_review: int,
    pending_revenue: float,
    review_mode: bool,
    latest_event: Mapping[str, Any] | None,
) -> str:
    if pending_review:
        return (
            f"{pending_review} approvals are waiting before the next high-trust move."
            if review_mode
            else f"{pending_review} approvals are waiting for operator review."
        )
    if pending_revenue:
        return f"{_format_currency(pending_revenue)} is sitting in pending revenue and ready to be cleared."
    if latest_event:
        return f"{_humanize_event_type(str(latest_event.get('event_type', 'activity')))} just moved. Runtime is clear."
    return "Runtime is quiet, healthy, and ready for the next command window."


def _build_priority_queue(
    *,
    pending_review: int,
    pending_revenue: float,
    emails_today: int,
    emails_week: int,
    total_leads: int,
    interested: int,
    closed: int,
    review_mode: bool,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = [
        {
            "eyebrow": "Needs You",
            "title": "Review Queue",
            "metric": str(pending_review),
            "detail": (
                f"{pending_review} approvals are waiting and Titan is pacing itself for operator signoff."
                if pending_review
                else "No approvals are blocking momentum right now."
            ),
            "footnote": "Review mode is active." if review_mode else "Autonomous guardrails are clear.",
            "tone": "warning" if pending_review else "success",
        },
        {
            "eyebrow": "Money",
            "title": "Revenue Ready",
            "metric": _format_currency(pending_revenue),
            "detail": (
                f"{_format_currency(pending_revenue)} is still pending across the deal queue."
                if pending_revenue
                else "No pending revenue is stranded in the current snapshot."
            ),
            "footnote": f"{closed} closed outcomes are already in the book.",
            "tone": "success" if pending_revenue else "default",
        },
        {
            "eyebrow": "Tempo",
            "title": "Outreach Cadence",
            "metric": f"{emails_today:,}",
            "detail": (
                f"{emails_today:,} emails went out today and {emails_week:,} in the trailing week."
            ),
            "footnote": (
                f"{interested} warm leads are active across {total_leads:,} tracked leads."
            ),
            "tone": "default",
        },
    ]
    return items


def _build_pipeline_stages(pipeline: Mapping[str, int]) -> list[dict[str, Any]]:
    total = sum(pipeline.values()) or 1
    ordered_statuses = [status for status in PIPELINE_ORDER if pipeline.get(status)]
    extras = sorted(status for status in pipeline if status not in PIPELINE_ORDER and pipeline.get(status))

    return [
        {
            "label": _humanize_status(status),
            "count": pipeline[status],
            "share": max(6, round((pipeline[status] / total) * 100)),
        }
        for status in [*ordered_statuses, *extras]
    ]


def _build_event_card(event: Mapping[str, Any]) -> dict[str, str]:
    event_type = str(event.get("event_type", "system_event"))
    return {
        "time": _format_event_time(event.get("created_at")),
        "title": _humanize_event_type(event_type),
        "detail": _event_payload_summary(event),
        "tone": _event_tone(event_type),
    }


def _event_payload_summary(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        ordered_keys = ("client", "business_name", "reason", "count", "amount", "details", "message")
        values = [payload[key] for key in ordered_keys if key in payload and payload[key] not in (None, "")]
        extras = [
            value for key, value in payload.items()
            if key not in ordered_keys and value not in (None, "")
        ]
        parts = [str(value) for value in [*values, *extras]]
        if parts:
            return " · ".join(parts[:3])
    if payload not in (None, ""):
        return str(payload)
    return "No additional detail provided."


def _event_tone(event_type: str) -> str:
    lowered = event_type.lower()
    if "review" in lowered or "pending" in lowered:
        return "warning"
    if any(token in lowered for token in ("error", "fail", "down", "blocked")):
        return "danger"
    if any(token in lowered for token in ("paid", "closed", "deployed", "healthy")):
        return "success"
    return "default"


def _close_rate(total_leads: int, closed: int) -> float:
    if total_leads <= 0:
        return 0.0
    return (closed / total_leads) * 100


def _format_currency(amount: float) -> str:
    return f"${float(amount):,.2f}"


def _format_sync_time(event: Mapping[str, Any] | None) -> str:
    if not event:
        return "Awaiting signal"
    return _format_event_time(event.get("created_at"))


def _format_event_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    return "Now"


def _humanize_status(status: str) -> str:
    return status.replace("_", " ").title()


def _humanize_event_type(event_type: str) -> str:
    return event_type.replace("_", " ").title()
