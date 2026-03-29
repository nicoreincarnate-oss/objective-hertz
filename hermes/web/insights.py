"""Strategic Q&A helpers for the Hermes dashboard."""

from __future__ import annotations

import json
from typing import Any

from shared.db import fetch_all
from shared.llm_client import llm


async def answer_strategic_question(question: str) -> dict[str, Any]:
    """Answer a strategic War Room question using grounded pipeline data."""
    normalized_question = " ".join((question or "").split()).strip()
    if not normalized_question:
        raise ValueError("Question cannot be empty.")

    industry_rows = await fetch_all(
        """
        SELECT
            COALESCE(NULLIF(TRIM(industry), ''), 'unknown') AS industry,
            COUNT(*) AS total_leads,
            COUNT(*) FILTER (WHERE status = 'interested') AS interested,
            COUNT(*) FILTER (WHERE status IN ('closed','building','deployed','invoiced','paid')) AS closed,
            COALESCE(AVG(lead_score), 0) AS avg_lead_score
        FROM clients
        GROUP BY industry
        ORDER BY closed DESC, interested DESC, total_leads DESC
        LIMIT 12
        """
    )

    recent_learnings = await fetch_all(
        """
        SELECT category, insight, confidence
        FROM titan_learnings
        ORDER BY confidence DESC, created_at DESC
        LIMIT 10
        """
    )

    prompt = f"""You are Hermes in the War Room. Answer the operator's strategic question using only the grounded evidence below.

QUESTION:
{normalized_question}

PIPELINE DATA:
{json.dumps([dict(row) for row in industry_rows], default=str, indent=2)}

RECENT LEARNINGS:
{json.dumps([dict(row) for row in recent_learnings], default=str, indent=2)}

Return JSON with this shape:
{{
  "answer": "2-4 sentence direct answer grounded in the evidence",
  "evidence": ["fact 1", "fact 2"],
  "recommended_actions": ["action 1", "action 2"]
}}

Rules:
- Be specific and concise.
- Only claim what the data supports.
- If the evidence is weak, say so clearly.
"""

    result = await llm.generate(
        prompt,
        model="smart",
        temperature=0.2,
        max_tokens=500,
        use_dna=True,
        daemon_name="hermes",
    )

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError("No JSON object found in LLM response")
        parsed = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        parsed = {
            "answer": "The strategic view is temporarily unavailable.",
            "evidence": [],
            "recommended_actions": [],
        }

    return {
        "answer": str(parsed.get("answer", "")).strip(),
        "evidence": [str(item).strip() for item in parsed.get("evidence", []) if str(item).strip()],
        "recommended_actions": [
            str(item).strip() for item in parsed.get("recommended_actions", []) if str(item).strip()
        ],
        "question": normalized_question,
    }
