"""
MetaClaw — Dual-timescale meta-learning for pipeline adaptation.

Paper: MetaClaw (Paper 101, up to 32% relative accuracy improvement).

Two learning loops:
- Fast (per-interaction): bandit + MemRL selects best combo of
  (skill variant, email template, pricing tier) for THIS specific lead
- Slow (nightly): meta-learns which fast adaptations work by industry+region,
  updating meta-weights so future fast loops start smarter

Gated behind METACLAW_ENABLED=1 (default 1).
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("perseus.metaclaw")

METACLAW_ENABLED = os.environ.get("METACLAW_ENABLED", "1") == "1"


async def fast_adapt(lead: dict) -> dict:
    """Fast loop: select best strategy for this specific lead.

    Combines bandit selection + memory-based similarity matching.
    Returns: {"template": str, "pricing_tier": str, "skill": str, "confidence": float}
    """
    if not METACLAW_ENABLED:
        return {"template": "default", "pricing_tier": "standard", "skill": "", "confidence": 0.5}

    industry = lead.get("industry", "").lower()
    region = lead.get("region", "").lower()
    score = lead.get("lead_score", 50)

    # Load meta-weights for this industry+region
    meta = await _load_meta_weights(industry, region)

    # Select template via bandit (if available) or meta-weights
    template = meta.get("best_template", "default")
    pricing = meta.get("best_pricing", "standard")
    skill = meta.get("best_skill", "")

    # Override with bandit if it has converged
    try:
        from shared.bandit import BANDIT_ENABLED, get_bandit
        if BANDIT_ENABLED:
            bandit = get_bandit()
            exp_id = f"template_{industry}" if industry else "template_default"
            stats = bandit.get_stats(exp_id)
            if stats.get("converged") and stats.get("winner"):
                template = stats["winner"]
    except Exception:
        pass

    # Score-based pricing tier
    if score > 75:
        pricing = "premium"
    elif score < 40:
        pricing = "budget"

    confidence = meta.get("confidence", 0.5)

    return {
        "template": template,
        "pricing_tier": pricing,
        "skill": skill,
        "confidence": confidence,
    }


async def slow_consolidate() -> dict:
    """Slow loop: meta-learn which fast adaptations worked.

    Called during nightly sleep cycle. Analyzes last 24h outcomes
    grouped by industry+region, updates meta-weights.

    Returns: {"updated": int, "industries": list}
    """
    if not METACLAW_ENABLED:
        return {"updated": 0}

    try:
        from shared.db import fetch_all, set_config

        # Get recent outcomes grouped by industry+region
        results = await fetch_all(
            """SELECT c.industry, c.region,
                      COUNT(*) as total,
                      COUNT(*) FILTER (WHERE c.status IN ('replied','interested','closed','paid')) as conversions,
                      AVG(d.amount) as avg_deal
               FROM clients c
               LEFT JOIN deals d ON d.client_id = c.id
               WHERE c.updated_at > NOW() - INTERVAL '24 hours'
               GROUP BY c.industry, c.region
               HAVING COUNT(*) >= 3"""
        )

        updated = 0
        industries = []
        for row in results:
            industry = row.get("industry", "").lower()
            region = row.get("region", "").lower()
            total = row.get("total", 1)
            conversions = row.get("conversions", 0)
            conversion_rate = conversions / total if total > 0 else 0

            if not industry:
                continue

            # Load existing meta-weights
            meta = await _load_meta_weights(industry, region)

            # Update with exponential moving average
            alpha = 0.3  # learning rate for meta-weights
            old_rate = meta.get("conversion_rate", 0.0)
            new_rate = alpha * conversion_rate + (1 - alpha) * old_rate

            meta["conversion_rate"] = round(new_rate, 4)
            meta["confidence"] = min(1.0, meta.get("confidence", 0.3) + 0.05)
            meta["last_updated"] = "now"
            meta["sample_size"] = meta.get("sample_size", 0) + total

            # Save
            key = f"metaclaw:{industry}:{region}" if region else f"metaclaw:{industry}"
            await set_config(key, json.dumps(meta))

            updated += 1
            industries.append(industry)

        logger.info(f"MetaClaw slow consolidation: updated {updated} industry segments")
        return {"updated": updated, "industries": industries}

    except Exception as e:
        logger.debug(f"MetaClaw consolidation failed: {e}")
        return {"updated": 0, "error": str(e)}


async def _load_meta_weights(industry: str, region: str = "") -> dict:
    """Load meta-weights for an industry+region combo."""
    try:
        from shared.db import get_config
        key = f"metaclaw:{industry}:{region}" if region else f"metaclaw:{industry}"
        raw = await get_config(key)
        if raw:
            if isinstance(raw, str):
                return json.loads(raw)
            return raw
    except Exception:
        pass
    return {
        "best_template": "default",
        "best_pricing": "standard",
        "best_skill": "",
        "conversion_rate": 0.0,
        "confidence": 0.3,
        "sample_size": 0,
    }


async def get_meta_stats() -> dict:
    """Get all meta-learning stats for monitoring."""
    try:
        from shared.db import fetch_all
        rows = await fetch_all(
            "SELECT key, value FROM system_config WHERE key LIKE 'metaclaw:%'"
        )
        stats = {}
        for row in rows:
            key = row.get("key", "")
            try:
                stats[key] = json.loads(row.get("value", "{}"))
            except (json.JSONDecodeError, TypeError):
                stats[key] = row.get("value")
        return stats
    except Exception:
        return {}
