"""
Scientific Method Loop — Hypothesis-driven experimentation in backprop.

Paper: Exploring the Role of LLMs in Scientific Method (Paper 96).

Every backprop edit becomes a tracked experiment:
1. Hypothesis: "Changing X will improve metric Y by Z%"
2. Apply change
3. Wait 48 hours (or N leads processed)
4. Evaluate: did the metric actually change?
5. Confirmed → boost confidence. Refuted → auto-rollback.

Replaces "apply and hope" with structured experimentation.

Gated behind SCIENTIFIC_LOOP_ENABLED=1 (default 1).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger("perseus.scientific_loop")

SCIENTIFIC_LOOP_ENABLED = os.environ.get("SCIENTIFIC_LOOP_ENABLED", "1") == "1"
EVALUATION_DELAY_HOURS = 48
MIN_LEADS_FOR_EVALUATION = 20


async def create_experiment(
    hypothesis: str,
    change_description: str,
    metric_name: str,
    baseline_value: float,
    expected_delta: float,
    cycle_id: int = 0,
) -> int | None:
    """Record a new experiment hypothesis before applying a change.

    Returns experiment ID or None if feature disabled.
    """
    if not SCIENTIFIC_LOOP_ENABLED:
        return None

    try:
        from shared.db import execute, fetch_val
        await execute(
            """INSERT INTO experiments
               (hypothesis, change_description, metric_name, baseline_value, expected_delta, cycle_id, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'active')""",
            (hypothesis, change_description, metric_name, baseline_value, expected_delta, cycle_id),
        )
        exp_id = await fetch_val("SELECT MAX(id) FROM experiments")
        logger.info(f"Experiment {exp_id}: {hypothesis[:80]}")
        return exp_id
    except Exception as e:
        logger.debug(f"Create experiment failed: {e}")
        return None


async def evaluate_experiments() -> list[dict]:
    """Evaluate all active experiments that are past their evaluation window.

    Called during sleep cycle. Checks if hypothesis was confirmed or refuted.
    Returns list of evaluation results.
    """
    if not SCIENTIFIC_LOOP_ENABLED:
        return []

    try:
        from shared.db import execute, fetch_all

        # Find experiments ready for evaluation
        cutoff = (datetime.now() - timedelta(hours=EVALUATION_DELAY_HOURS)).isoformat()
        active = await fetch_all(
            """SELECT id, hypothesis, metric_name, baseline_value, expected_delta, cycle_id
               FROM experiments
               WHERE status = 'active' AND created_at < %s""",
            (cutoff,),
        )

        results = []
        for exp in active:
            metric = exp.get("metric_name", "")
            baseline = exp.get("baseline_value", 0)
            expected = exp.get("expected_delta", 0)

            # Get current metric value
            current = await _get_metric_value(metric)
            if current is None:
                continue

            actual_delta = current - baseline
            confirmed = (expected > 0 and actual_delta > expected * 0.5) or \
                       (expected < 0 and actual_delta < expected * 0.5)

            status = "confirmed" if confirmed else "refuted"

            # Update experiment
            await execute(
                """UPDATE experiments
                   SET status = %s, actual_delta = %s, evaluated_at = NOW()
                   WHERE id = %s""",
                (status, actual_delta, exp["id"]),
            )

            result = {
                "experiment_id": exp["id"],
                "hypothesis": exp.get("hypothesis", ""),
                "status": status,
                "expected_delta": expected,
                "actual_delta": round(actual_delta, 4),
                "metric": metric,
            }
            results.append(result)

            if confirmed:
                logger.info(f"Experiment {exp['id']} CONFIRMED: {exp.get('hypothesis', '')[:60]}")
            else:
                logger.warning(f"Experiment {exp['id']} REFUTED: expected {expected}, got {actual_delta:.4f}")
                # Auto-rollback if available
                cycle_id = exp.get("cycle_id")
                if cycle_id:
                    try:
                        from perseus.backprop import rollback_cycle
                        await rollback_cycle(cycle_id)
                        result["rolled_back"] = True
                    except Exception:
                        result["rolled_back"] = False

        return results

    except Exception as e:
        logger.debug(f"Experiment evaluation failed: {e}")
        return []


async def _get_metric_value(metric_name: str) -> float | None:
    """Get current value for a named metric."""
    try:
        from shared.db import fetch_val

        metric_queries = {
            "reply_rate": """SELECT CASE WHEN COUNT(*)>0 THEN
                COUNT(*) FILTER (WHERE status IN ('replied','interested','closed','paid'))::float / COUNT(*)
                ELSE 0 END FROM clients WHERE created_at > NOW() - INTERVAL '7 days'""",
            "close_rate": """SELECT CASE WHEN COUNT(*)>0 THEN
                COUNT(*) FILTER (WHERE status IN ('closed','paid'))::float / COUNT(*)
                ELSE 0 END FROM clients WHERE created_at > NOW() - INTERVAL '14 days'""",
            "avg_deal_amount": "SELECT COALESCE(AVG(amount), 0) FROM deals WHERE created_at > NOW() - INTERVAL '7 days'",
            "bounce_rate": """SELECT CASE WHEN COUNT(*)>0 THEN
                COUNT(*) FILTER (WHERE status = 'lost')::float / COUNT(*)
                ELSE 0 END FROM clients WHERE created_at > NOW() - INTERVAL '7 days'""",
        }

        query = metric_queries.get(metric_name)
        if not query:
            return None

        return await fetch_val(query)

    except Exception:
        return None


async def get_active_experiments() -> list[dict]:
    """Get all active experiments for monitoring."""
    try:
        from shared.db import fetch_all
        return await fetch_all(
            "SELECT * FROM experiments WHERE status = 'active' ORDER BY created_at DESC"
        )
    except Exception:
        return []
