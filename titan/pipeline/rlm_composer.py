"""
RLM (Recursive Language Model) Composer — recursive context retrieval for email composition.

Implements a draft->evaluate->refine loop (max 3 iterations) that:
1. Generates an email draft using lead research + retrieved context
2. Evaluates via AntiSlopScorer (Haiku, 5 dimensions)
3. Identifies weak dimensions and expands context targeting them
4. Returns the highest-scoring version (not necessarily the latest)

Budget-aware: enforces per-email ($0.08) and monthly ($100) spend caps.
Context sources: Mem0 (write path for research), Qdrant (read path for retrieval).

Feature flag: ENABLE_RLM env var (true/1 to enable).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

from shared.anti_slop import AntiSlopScorer, _composite_score, _is_good_enough
from shared.llm_client import llm

logger = logging.getLogger("titan.rlm_composer")

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Check whether the RLM composer is active."""
    return os.environ.get("ENABLE_RLM", "").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# Optional dependencies (Mem0, Qdrant) — graceful degradation
# ---------------------------------------------------------------------------

_mem0_client = None
_qdrant_client = None

try:
    from mem0 import MemoryClient as _Mem0MemoryClient  # type: ignore[import-untyped]
    _mem0_client = _Mem0MemoryClient()
    logger.info("Mem0 client initialized for RLM context storage")
except ImportError:
    logger.warning("mem0 not available — research context storage will be skipped")
except Exception as exc:
    logger.warning("Mem0 client init failed: %s — continuing without", exc)

try:
    from qdrant_client import QdrantClient as _QdrantClient  # type: ignore[import-untyped]
    _qdrant_host = os.environ.get("QDRANT_HOST", "localhost")
    _qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    _qdrant_client = _QdrantClient(host=_qdrant_host, port=_qdrant_port)
    logger.info("Qdrant client initialized for RLM context retrieval")
except ImportError:
    logger.warning("qdrant_client not available — vector context retrieval will be skipped")
except Exception as exc:
    logger.warning("Qdrant client init failed: %s — continuing without", exc)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 3
PER_EMAIL_BUDGET_USD = 0.08
MONTHLY_BUDGET_USD = 100.0
QDRANT_COLLECTION = "titan_research"
QDRANT_TOP_K = 10

# Dimension -> context expansion strategy
_EXPANSION_STRATEGIES: dict[str, str] = {
    "specificity": "competitor data, pricing details, market stats for {industry}",
    "authenticity": "local business context, owner background for {business}",
    "clarity": "successful email examples for {industry}",
    "value_density": "ROI data, case studies for {industry}",
}

# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------

_session_email_cost: float = 0.0


async def _check_budget(current_email_cost: float = 0.0) -> tuple[bool, str]:
    """Check per-email and monthly RLM spend caps.

    Returns (within_budget, reason).
    """
    # Per-email check
    if current_email_cost >= PER_EMAIL_BUDGET_USD:
        return False, f"per-email cap reached (${current_email_cost:.4f} >= ${PER_EMAIL_BUDGET_USD})"

    # Monthly check via llm_metrics table
    try:
        from shared.observability import get_metrics_summary

        summary = await get_metrics_summary(daemon="titan", hours=720)  # ~30 days
        daemons = summary.get("daemons", [])
        titan_cost = 0.0
        for d in daemons:
            if d.get("daemon") == "titan":
                titan_cost = float(d.get("total_cost_usd", 0) or 0)
                break
        if titan_cost >= MONTHLY_BUDGET_USD:
            return False, f"monthly cap reached (${titan_cost:.2f} >= ${MONTHLY_BUDGET_USD})"
    except Exception as exc:
        logger.warning("Budget check DB query failed (allowing): %s", exc)

    return True, "ok"


# ---------------------------------------------------------------------------
# Mem0 write path — store research context
# ---------------------------------------------------------------------------

async def store_research_context(lead_id: int, research: dict[str, Any]) -> str | None:
    """Store research sections in Mem0 per lead.

    Returns mem0_context_id linking to the leads table,
    or None if Mem0 is unavailable.
    """
    if _mem0_client is None:
        logger.warning("Mem0 unavailable — skipping research context storage for lead %s", lead_id)
        return None

    context_id = f"lead-{lead_id}-{uuid.uuid4().hex[:8]}"

    try:
        # Build a rich text representation of the research
        sections = []
        for key, value in research.items():
            if value:
                sections.append(f"## {key}\n{value}")
        research_text = "\n\n".join(sections) if sections else str(research)

        _mem0_client.add(
            research_text,
            user_id=context_id,
            metadata={
                "lead_id": lead_id,
                "type": "lead_research",
                "stored_at": time.time(),
            },
        )

        # Update leads table with the context_id
        try:
            from shared.db import execute
            await execute(
                "UPDATE clients SET mem0_context_id = %s WHERE id = %s",
                (context_id, lead_id),
            )
        except Exception as db_exc:
            logger.warning("Failed to update leads.mem0_context_id for %s: %s", lead_id, db_exc)

        logger.info("Stored research context in Mem0 for lead %s (context_id=%s)", lead_id, context_id)
        return context_id

    except Exception as exc:
        logger.warning("Mem0 store failed for lead %s: %s", lead_id, exc)
        return None


# ---------------------------------------------------------------------------
# Qdrant read path — retrieve and expand context
# ---------------------------------------------------------------------------

async def _retrieve_context(lead: dict, research: dict[str, Any]) -> list[str]:
    """Query Qdrant with lead company/industry/pain points.

    Returns top-K relevant context items as strings.
    Falls back to empty list if Qdrant is unavailable.
    """
    if _qdrant_client is None:
        return []

    try:
        query_text = " ".join([
            str(lead.get("business_name", "")),
            str(lead.get("industry", "")),
            str(research.get("pain_points", "")),
            str(research.get("research_summary", "")),
        ]).strip()

        if not query_text:
            return []

        # Use Qdrant's search with text query
        results = _qdrant_client.query(
            collection_name=QDRANT_COLLECTION,
            query_text=query_text,
            limit=QDRANT_TOP_K,
        )

        context_items = []
        for result in results:
            payload = getattr(result, "metadata", {}) or {}
            text = payload.get("text", "") or payload.get("content", "")
            if text:
                context_items.append(str(text))

        logger.info("Retrieved %d context items from Qdrant for lead %s", len(context_items), lead.get("id"))
        return context_items

    except Exception as exc:
        logger.warning("Qdrant retrieval failed for lead %s: %s", lead.get("id"), exc)
        return []


async def _expand_context(
    lead: dict,
    research: dict[str, Any],
    weak_dimension: str,
) -> list[str]:
    """Expand context targeting a specific weak dimension.

    E.g., if specificity < 0.6, pull competitor data and pricing.
    """
    if _qdrant_client is None:
        return []

    strategy = _EXPANSION_STRATEGIES.get(weak_dimension, "")
    if not strategy:
        return []

    query = strategy.format(
        industry=lead.get("industry", "general"),
        business=lead.get("business_name", "business"),
    )

    try:
        results = _qdrant_client.query(
            collection_name=QDRANT_COLLECTION,
            query_text=query,
            limit=5,
        )
        items = []
        for result in results:
            payload = getattr(result, "metadata", {}) or {}
            text = payload.get("text", "") or payload.get("content", "")
            if text:
                items.append(str(text))
        return items

    except Exception as exc:
        logger.warning("Qdrant context expansion failed for %s: %s", weak_dimension, exc)
        return []


def _find_weakest_dimension(scores: dict[str, float]) -> str:
    """Find the lowest-scoring dimension (excluding slop_score, which is inverted)."""
    evaluable = {
        k: v for k, v in scores.items()
        if k != "slop_score"
    }
    if not evaluable:
        return "specificity"
    return min(evaluable, key=lambda k: evaluable[k])


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------

async def _generate_draft(
    lead: dict,
    research: dict[str, Any],
    context: list[str],
    previous_feedback: str | None = None,
    iteration: int = 0,
) -> tuple[str, float]:
    """Build prompt with specific business details and generate a draft.

    Returns (draft_text, estimated_cost_usd).
    """
    context_block = ""
    if context:
        context_block = "\n\nRELEVANT CONTEXT:\n" + "\n---\n".join(context[:5])

    feedback_block = ""
    if previous_feedback:
        feedback_block = f"\n\nPREVIOUS FEEDBACK (iteration {iteration}):\n{previous_feedback}"

    prompt = f"""You are Titan's email copywriter. Write a cold outreach email.

LEAD INFORMATION:
- Business: {lead.get('business_name', 'Unknown Business')}
- Contact: {lead.get('contact_name', 'Business Owner')}
- Industry: {lead.get('industry', 'unknown')}
- Location: {lead.get('city', '')}, {lead.get('country', '')}
- Research: {research.get('research_summary', lead.get('research_summary', 'No research available'))}
{context_block}
{feedback_block}

REQUIREMENTS:
- 100% unique — never a template
- Short (under 150 words)
- Personalized to their specific business
- Clear value proposition (professional website)
- One clear CTA (reply to discuss)
- No spam triggers, no ALL CAPS, no excessive punctuation
- Subject line under 50 characters
- Reference specific details from research

Return JSON:
{{
    "subject": "...",
    "body": "..."
}}"""

    start = time.perf_counter()
    # Use smart model for high-score leads, fast for others
    lead_score = float(lead.get("lead_score", 0) or 0)
    model = "smart" if lead_score >= 80 else "fast"

    result = await llm.generate(prompt, model=model, temperature=0.8, operation="titan._generate_draft", daemon_name="titan")
    elapsed = time.perf_counter() - start

    # Estimate cost: ~$0.003 per 1K tokens for Haiku, ~$0.015 for Sonnet
    # Rough estimate: prompt ~500 tokens, response ~200 tokens
    est_cost = 0.005 if model == "fast" else 0.015

    logger.debug(
        "Draft generated (iteration %d, model=%s, %.2fs, ~$%.4f)",
        iteration, model, elapsed, est_cost,
    )
    return result, est_cost


def _format_feedback(scores: dict[str, float]) -> str:
    """Format scores into actionable feedback for the next iteration."""
    issues = []
    if scores.get("slop_score", 0) > 0.2:
        issues.append("Too many cliches. Use specific, concrete language instead of buzzwords.")
    if scores.get("clarity", 1) < 0.7:
        issues.append("Writing is unclear. Simplify sentence structure and be direct.")
    if scores.get("specificity", 1) < 0.7:
        issues.append("Too vague. Reference specific details from the research.")
    if scores.get("authenticity", 1) < 0.7:
        issues.append("Sounds like generic AI output. Make it conversational and human.")
    if scores.get("value_density", 1) < 0.7:
        issues.append("Too much filler. Every sentence must add value.")
    return "\n".join(issues) if issues else "Improve overall quality."


# ---------------------------------------------------------------------------
# RLMComposer — main class
# ---------------------------------------------------------------------------

class RLMComposer:
    """Recursive context retrieval composer for email content.

    draft->evaluate->refine loop, max 3 iterations.
    Returns the highest-scoring version across all iterations.
    Budget-aware: enforces per-email and monthly spend caps.
    """

    def __init__(self) -> None:
        self._scorer = AntiSlopScorer()

    async def compose(
        self,
        lead: dict,
        research: dict[str, Any],
        template: str | None = None,
    ) -> dict[str, Any]:
        """Run the recursive composition loop.

        Returns dict with:
          - subject: str
          - body: str
          - scores: dict[str, float]
          - composite: float
          - iterations: int
          - budget_used: float
        """
        import json as _json

        # Pre-flight budget check
        within_budget, reason = await _check_budget()
        if not within_budget:
            logger.warning("RLM budget exceeded before start: %s", reason)
            return {
                "subject": "",
                "body": "",
                "scores": {},
                "composite": 0.0,
                "iterations": 0,
                "budget_used": 0.0,
                "budget_exceeded": True,
                "reason": reason,
            }

        # Initial context retrieval
        context = await _retrieve_context(lead, research)
        total_cost = 0.0

        # Track all versions: (subject, body, scores)
        versions: list[tuple[str, str, dict[str, float]]] = []

        for iteration in range(MAX_ITERATIONS):
            # Budget check per iteration
            within_budget, reason = await _check_budget(total_cost)
            if not within_budget:
                logger.info("RLM budget hit during iteration %d: %s", iteration, reason)
                break

            # Generate feedback from previous scores
            feedback = None
            if versions:
                feedback = _format_feedback(versions[-1][2])

            # Generate draft
            draft_text, est_cost = await _generate_draft(
                lead, research, context, feedback, iteration,
            )
            total_cost += est_cost

            # Parse the draft
            try:
                start_idx = draft_text.find("{")
                end_idx = draft_text.rfind("}") + 1
                if start_idx >= 0 and end_idx > start_idx:
                    parsed = _json.loads(draft_text[start_idx:end_idx])
                else:
                    logger.warning("No JSON found in draft (iteration %d)", iteration)
                    continue
            except (_json.JSONDecodeError, ValueError) as exc:
                logger.warning("Draft JSON parse failed (iteration %d): %s", iteration, exc)
                continue

            subject = parsed.get("subject", "")
            body = parsed.get("body", "")
            if not body:
                continue

            # Evaluate with AntiSlopScorer
            combined_content = f"{subject}\n\n{body}"
            scores = await self._scorer.score(combined_content, context="email")
            total_cost += 0.003  # Haiku evaluation cost estimate

            versions.append((subject, body, scores))

            # Check if good enough
            if _is_good_enough(scores, "email"):
                logger.info(
                    "RLM reached good-enough at iteration %d (composite=%.3f)",
                    iteration, _composite_score(scores),
                )
                break

            # Expand context targeting weakest dimension
            if iteration < MAX_ITERATIONS - 1:
                weak = _find_weakest_dimension(scores)
                expanded = await _expand_context(lead, research, weak)
                if expanded:
                    context.extend(expanded)
                    logger.debug("Expanded context for %s (+%d items)", weak, len(expanded))

        # Return highest-scoring version
        if not versions:
            return {
                "subject": "",
                "body": "",
                "scores": {},
                "composite": 0.0,
                "iterations": 0,
                "budget_used": total_cost,
            }

        best = max(versions, key=lambda v: _composite_score(v[2]))
        return {
            "subject": best[0],
            "body": best[1],
            "scores": best[2],
            "composite": _composite_score(best[2]),
            "iterations": len(versions),
            "budget_used": total_cost,
        }
