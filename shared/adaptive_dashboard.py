"""
Adaptive Dashboard — Auto-adapt War Room layout based on system state.

Papers: PrototypeFlow (Paper 88, iterative LLM-driven refinement),
UX Design Without Designers (Paper 89, real-time UI adaptation).

Dashboard layout changes based on what's most important right now:
- High error rate → promote Health panel
- Pipeline bottleneck → promote that stage's metrics
- Deals waiting → promote negotiation panel
- Default → revenue-first layout

10-minute cooldown on layout changes to prevent thrashing.

Gated behind ADAPTIVE_DASHBOARD_ENABLED=1 (default 1).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("perseus.adaptive_dashboard")

ADAPTIVE_DASHBOARD_ENABLED = os.environ.get("ADAPTIVE_DASHBOARD_ENABLED", "1") == "1"
LAYOUT_COOLDOWN_SECONDS = 600  # 10 minutes between layout changes
_last_layout_change: float = 0.0
_current_layout: str = "revenue"


def compute_dashboard_layout(system_state: dict) -> dict:
    """Compute optimal dashboard layout based on current system state.

    Returns: {
        "layout": str (primary layout name),
        "promoted_panels": list[str],
        "demoted_panels": list[str],
        "alerts": list[str],
        "css_class": str (for frontend),
    }
    """
    global _last_layout_change, _current_layout

    if not ADAPTIVE_DASHBOARD_ENABLED:
        return {"layout": "revenue", "promoted_panels": [], "demoted_panels": [],
                "alerts": [], "css_class": "layout-revenue"}

    # Cooldown check
    now = time.time()
    if now - _last_layout_change < LAYOUT_COOLDOWN_SECONDS:
        return {"layout": _current_layout, "promoted_panels": [], "demoted_panels": [],
                "alerts": [], "css_class": f"layout-{_current_layout}"}

    error_rate = system_state.get("error_rate", 0)
    bottleneck_stage = system_state.get("bottleneck_stage", "")
    pending_negotiations = system_state.get("pending_negotiations", 0)
    active_leads = system_state.get("active_leads", 0)
    revenue_today = system_state.get("revenue_today", 0)
    budget_pct = system_state.get("budget_remaining_pct", 1.0)

    promoted = []
    demoted = []
    alerts = []
    layout = "revenue"  # default

    # Rule 1: Error rate > 5% → health layout
    if error_rate > 0.05:
        layout = "health"
        promoted.append("health_panel")
        promoted.append("error_details")
        demoted.append("revenue_chart")
        alerts.append(f"Error rate {error_rate:.1%} — check pipeline health")

    # Rule 2: Pipeline bottleneck → promote that stage
    elif bottleneck_stage:
        layout = "bottleneck"
        promoted.append(f"{bottleneck_stage}_metrics")
        promoted.append("pipeline_flow")
        alerts.append(f"Bottleneck at {bottleneck_stage}")

    # Rule 3: Deals waiting → negotiation layout
    elif pending_negotiations > 0:
        layout = "negotiation"
        promoted.append("negotiation_panel")
        promoted.append("deal_pipeline")
        alerts.append(f"{pending_negotiations} deals in negotiation")

    # Rule 4: Budget pressure → budget layout
    elif budget_pct < 0.2:
        layout = "budget"
        promoted.append("budget_panel")
        promoted.append("spend_breakdown")
        alerts.append(f"Budget at {budget_pct:.0%}")

    # Default: revenue layout
    else:
        layout = "revenue"
        promoted.append("revenue_chart")
        promoted.append("pipeline_summary")

    if layout != _current_layout:
        _last_layout_change = now
        _current_layout = layout
        logger.info(f"Dashboard layout changed: {layout} (promoted: {promoted})")

    return {
        "layout": layout,
        "promoted_panels": promoted,
        "demoted_panels": demoted,
        "alerts": alerts,
        "css_class": f"layout-{layout}",
    }


def detect_bottleneck(pipeline_counts: dict) -> str:
    """Detect which pipeline stage is the bottleneck.

    Bottleneck = stage with disproportionately many leads stuck.
    Returns stage name or "" if no bottleneck.
    """
    if not pipeline_counts:
        return ""

    total = sum(pipeline_counts.values())
    if total == 0:
        return ""

    for stage, count in pipeline_counts.items():
        # If >40% of active leads are stuck at one stage, it's a bottleneck
        if count / total > 0.4 and count > 5:
            return stage

    return ""
