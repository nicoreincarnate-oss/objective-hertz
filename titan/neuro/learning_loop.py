"""Closed-loop learning: daily reflection, neural rule extraction, segment profiles.

- neural_reflection(): daily correlation analysis (neuro-scores vs conversion)
- _create_neural_rule(): auto-extract rules at p<0.05, n>=30
- compute_segment_profiles(): weekly per-industry neural profiles
- _update_composite_weights(): shift weights toward predictive dimensions

Scheduled via Perseus: daily reflection at 03:00, weekly profiles at 04:00 Sunday.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np

try:
    from scipy.stats import pearsonr as _scipy_pearsonr

    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False


def _pearsonr(x: list[float], y: list[float]) -> tuple[float, float]:
    """Compute Pearson correlation with scipy fallback to numpy."""
    if _SCIPY_AVAILABLE:
        r, p = _scipy_pearsonr(x, y)
        return float(r), float(p)
    # Pure numpy fallback (no p-value computation -- return 1.0)
    arr_x = np.array(x, dtype=float)
    arr_y = np.array(y, dtype=float)
    if len(arr_x) < 3:
        return 0.0, 1.0
    corr_matrix = np.corrcoef(arr_x, arr_y)
    r = float(corr_matrix[0, 1])
    if np.isnan(r):
        return 0.0, 1.0
    # Approximate p-value using t-distribution approximation
    n = len(arr_x)
    if abs(r) >= 1.0:
        return r, 0.0
    t_stat = r * np.sqrt((n - 2) / (1 - r ** 2))
    # Two-tailed p-value approximation (rough but functional)
    p = float(2.0 * np.exp(-0.717 * abs(t_stat) - 0.416 * t_stat ** 2))
    p = max(0.0, min(1.0, p))
    return r, p

logger = logging.getLogger("neuro.learning_loop")

# Core dimensions in canonical order
_DIMENSIONS = ["self_relevance", "trust", "cognitive_ease", "emotional_resonance"]


# ---------------------------------------------------------------------------
# Daily reflection
# ---------------------------------------------------------------------------

async def neural_reflection() -> dict[str, Any]:
    """Daily reflection: correlate neuro-scores with conversion outcomes.

    Run via Perseus scheduler daily after 50+ scored emails exist.
    Computes Pearson correlation between each dimension score and binary
    conversion outcome.  Auto-extracts rules when significant.

    Returns:
        dict with status, correlations, and any rules created.
    """
    try:
        from shared.db import fetch_all
    except ImportError:
        logger.warning("shared.db unavailable -- returning mock reflection")
        return {"status": "db_unavailable"}

    try:
        data = await fetch_all("""
            SELECT
                es.neuro_scores,
                CASE WHEN c.status IN ('replied', 'interested', 'demo_built',
                                        'proposal_sent', 'negotiating', 'closed', 'paid')
                     THEN 1 ELSE 0 END as converted,
                c.industry, c.lead_score
            FROM email_sequences es
            JOIN clients c ON es.client_id = c.id
            WHERE es.neuro_scores IS NOT NULL
            AND es.sent_at > NOW() - INTERVAL '30 days'
        """)
    except Exception as exc:
        logger.error("Failed to fetch reflection data: %s", exc)
        return {"status": "query_error", "error": str(exc)}

    if len(data) < 50:
        return {"status": "insufficient_data", "count": len(data)}

    # Compute correlations per dimension
    correlations: dict[str, dict[str, float]] = {}
    rules_created: list[str] = []

    for dim in _DIMENSIONS:
        try:
            scores = [float(r["neuro_scores"][dim]) for r in data]
            outcomes = [r["converted"] for r in data]
            corr, p_value = _pearsonr(scores, outcomes)
            correlations[dim] = {
                "r": round(float(corr), 4),
                "p": round(float(p_value), 4),
                "n": len(data),
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping dimension %s: %s", dim, exc)
            correlations[dim] = {"r": 0.0, "p": 1.0, "n": 0}

    # Auto-extract rules when significant (NEURO-09)
    for dim, stats in correlations.items():
        if stats["p"] < 0.05 and stats["n"] >= 30:
            try:
                await _create_neural_rule(dim, stats)
                rules_created.append(dim)
            except Exception as exc:
                logger.error("Failed to create rule for %s: %s", dim, exc)

    # Update composite weights based on correlations
    try:
        await _update_composite_weights(correlations)
    except Exception as exc:
        logger.error("Failed to update weights: %s", exc)

    return {
        "status": "complete",
        "correlations": correlations,
        "rules_created": rules_created,
    }


# ---------------------------------------------------------------------------
# Neural rule creation
# ---------------------------------------------------------------------------

async def _create_neural_rule(dim: str, stats: dict[str, float]) -> None:
    """Auto-create titan_rule from statistically significant neural finding.

    Args:
        dim: Dimension name (e.g., "self_relevance").
        stats: dict with keys r, p, n from Pearson correlation.
    """
    from shared.db import execute

    direction = "positively" if stats["r"] > 0 else "negatively"
    rule_text = (
        f"Neuro-scorer: {dim} (r={stats['r']:.2f}, p={stats['p']:.3f}, "
        f"n={stats['n']}) {direction} correlates with conversion. "
        f"{'Prioritize' if stats['r'] > 0 else 'Minimize'} {dim} in compose prompts."
    )
    await execute(
        """INSERT INTO titan_rules (category, rule_text, source, confidence)
           VALUES ('neural_optimization', %s, 'neuro_learning_loop', %s)
           ON CONFLICT DO NOTHING""",
        (rule_text, abs(stats["r"])),
    )
    logger.info("Created neural rule for %s: r=%.2f, p=%.3f", dim, stats["r"], stats["p"])


# ---------------------------------------------------------------------------
# Composite weight updating
# ---------------------------------------------------------------------------

async def _update_composite_weights(correlations: dict[str, dict[str, float]]) -> None:
    """Shift composite weights toward conversion-predictive dimensions.

    Only updates when at least one dimension has p < 0.1.
    Dimensions without signal fall back to equal weighting (0.25).
    """
    total_abs_r = sum(
        abs(c["r"]) for c in correlations.values() if c["p"] < 0.1
    )
    if total_abs_r < 0.01:
        return  # No signal yet

    new_weights: dict[str, float] = {}
    for dim in _DIMENSIONS:
        stats = correlations.get(dim, {"r": 0.0, "p": 1.0})
        if stats["p"] < 0.1:
            new_weights[dim] = abs(stats["r"]) / total_abs_r
        else:
            new_weights[dim] = 0.25  # fallback to equal weight

    # Renormalize so weights sum to 1.0
    total = sum(new_weights.values())
    if total > 0:
        new_weights = {k: v / total for k, v in new_weights.items()}

    try:
        from shared.db import set_config

        await set_config("neuro_composite_weights", new_weights)
        logger.info("Updated composite weights: %s", new_weights)
    except ImportError:
        logger.warning("shared.db.set_config unavailable -- weights not persisted")


# ---------------------------------------------------------------------------
# Segment-specific neural profiles
# ---------------------------------------------------------------------------

async def compute_segment_profiles() -> dict[str, dict[str, float]]:
    """Compute ideal neural signature per industry segment.

    Run weekly. Requires 100+ scored emails per segment with positive outcomes.
    Returns dict mapping industry -> {dimension: avg_score}.
    """
    try:
        from shared.db import fetch_all
    except ImportError:
        logger.warning("shared.db unavailable -- returning empty profiles")
        return {}

    try:
        segments = await fetch_all("""
            SELECT c.industry,
                   AVG((es.neuro_scores->>'self_relevance')::float) as avg_sr,
                   AVG((es.neuro_scores->>'trust')::float) as avg_trust,
                   AVG((es.neuro_scores->>'cognitive_ease')::float) as avg_ease,
                   AVG((es.neuro_scores->>'emotional_resonance')::float) as avg_emo,
                   COUNT(*) as n
            FROM email_sequences es
            JOIN clients c ON es.client_id = c.id
            WHERE es.neuro_scores IS NOT NULL
            AND c.status IN ('replied', 'interested', 'closed', 'paid')
            GROUP BY c.industry
            HAVING COUNT(*) >= 100
        """)
    except Exception as exc:
        logger.error("Failed to fetch segment data: %s", exc)
        return {}

    profiles: dict[str, dict[str, float]] = {}

    for seg in segments:
        industry = seg["industry"]
        profiles[industry] = {
            "self_relevance": float(seg["avg_sr"]),
            "trust": float(seg["avg_trust"]),
            "cognitive_ease": float(seg["avg_ease"]),
            "emotional_resonance": float(seg["avg_emo"]),
        }

        # Store as titan_learning for MAGMA integration
        try:
            from shared.db import execute

            await execute(
                """INSERT INTO titan_learnings (category, key, value, source)
                   VALUES ('neural_profile', %s, %s, 'neuro_learning_loop')
                   ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value""",
                (f"neural_profile_{industry}", json.dumps(profiles[industry])),
            )
        except Exception as exc:
            logger.warning("Could not store profile for %s: %s", industry, exc)

    logger.info("Computed segment profiles for %d industries", len(profiles))
    return profiles


# ---------------------------------------------------------------------------
# Helper: compute correlations from raw data (for testing)
# ---------------------------------------------------------------------------

def compute_correlations(
    scores_by_dim: dict[str, list[float]],
    outcomes: list[int],
) -> dict[str, dict[str, float]]:
    """Compute dimension-outcome correlations from raw data.

    Testable without DB. Used by neural_reflection internally and
    directly in unit tests.
    """
    correlations: dict[str, dict[str, float]] = {}
    for dim in _DIMENSIONS:
        dim_scores = scores_by_dim.get(dim, [])
        if len(dim_scores) != len(outcomes) or len(dim_scores) < 3:
            correlations[dim] = {"r": 0.0, "p": 1.0, "n": 0}
            continue
        corr, p_value = _pearsonr(dim_scores, outcomes)
        correlations[dim] = {
            "r": round(float(corr), 4),
            "p": round(float(p_value), 4),
            "n": len(dim_scores),
        }
    return correlations
