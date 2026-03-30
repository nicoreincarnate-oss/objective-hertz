"""
Skill Distillation — Auto-extract reusable skills from successful pipelines.

Papers: SAGE (Paper 74, 3x SGC score, <50% tokens),
SkillRL (Paper 95, +15.3% over baselines, 10-20% token compression).

When a lead completes the full pipeline (discovered → paid), extract
the trajectory of all prompts/outputs and generalize into a reusable
SKILL.md file. Over time, email_compose preferentially loads
auto-distilled skills for matching industries.

Gated behind SKILL_DISTILL_ENABLED=1 (default 1).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("perseus.skill_distiller")

SKILL_DISTILL_ENABLED = os.environ.get("SKILL_DISTILL_ENABLED", "1") == "1"
MAX_DISTILLATIONS_PER_DAY = 1
_distillation_count_today: dict[str, int] = {}  # date_str -> count


async def distill_trajectory(client_id: int) -> dict | None:
    """Extract and generalize a successful pipeline trajectory into a skill.

    Called after a lead reaches status='paid'. Loads all interactions
    (emails, research, proposals) and asks LLM to generalize into
    a reusable template.

    Returns: {"skill_path": str, "industry": str, "stages": list} or None
    """
    if not SKILL_DISTILL_ENABLED:
        return None

    # Rate limit: max 1 distillation per day
    today = datetime.now().strftime("%Y-%m-%d")
    if _distillation_count_today.get(today, 0) >= MAX_DISTILLATIONS_PER_DAY:
        logger.debug("Skill distillation rate limited (1/day)")
        return None

    # Load trajectory data
    trajectory = await _load_trajectory(client_id)
    if not trajectory:
        return None

    # Generalize into skill template
    skill = await _generalize_trajectory(trajectory)
    if not skill:
        return None

    # Write SKILL.md
    skill_path = await _write_skill(skill, trajectory)
    if skill_path:
        _distillation_count_today[today] = _distillation_count_today.get(today, 0) + 1
        logger.info(f"Distilled skill from client {client_id}: {skill_path}")

    return {
        "skill_path": str(skill_path) if skill_path else None,
        "industry": trajectory.get("industry", ""),
        "stages": skill.get("stages_covered", []),
    }


async def _load_trajectory(client_id: int) -> dict | None:
    """Load all interactions for a completed deal."""
    try:
        from shared.db import fetch_all, fetch_one

        # Client info
        client = await fetch_one(
            "SELECT * FROM clients WHERE id = %s AND status = 'paid'",
            (client_id,),
        )
        if not client:
            return None

        # Email sequences
        emails = await fetch_all(
            "SELECT * FROM email_sequences WHERE client_id = %s ORDER BY created_at",
            (client_id,),
        )

        # Deal info
        deal = await fetch_one(
            "SELECT * FROM deals WHERE client_id = %s",
            (client_id,),
        )

        return {
            "client_id": client_id,
            "industry": client.get("industry", ""),
            "region": client.get("region", ""),
            "business_name": client.get("business_name", ""),
            "research_facts": client.get("research_facts", {}),
            "emails": [{"subject": e.get("subject", ""), "body": e.get("body", "")[:500]} for e in (emails or [])],
            "deal_amount": deal.get("amount", 0) if deal else 0,
            "demo_site_url": client.get("demo_site_url", ""),
        }
    except Exception as e:
        logger.debug(f"Trajectory load failed: {e}")
        return None


async def _generalize_trajectory(trajectory: dict) -> dict | None:
    """Ask LLM to generalize a specific trajectory into a reusable template."""
    try:
        from shared.llm_client import llm

        emails_text = "\n".join(
            f"Email {i+1}: {e.get('subject', '')} — {e.get('body', '')[:200]}"
            for i, e in enumerate(trajectory.get("emails", []))
        )

        result = await llm.generate(
            f"You are analyzing a SUCCESSFUL sales pipeline completion.\n\n"
            f"Industry: {trajectory.get('industry', 'unknown')}\n"
            f"Region: {trajectory.get('region', 'unknown')}\n"
            f"Deal amount: ${trajectory.get('deal_amount', 0)}\n"
            f"Research insights: {json.dumps(trajectory.get('research_facts', {}))[:500]}\n"
            f"Emails sent:\n{emails_text}\n\n"
            f"Generalize this into a reusable skill template. Extract:\n"
            f"1. What made this outreach effective for this industry?\n"
            f"2. Key phrases or approaches that worked\n"
            f"3. Research patterns to look for\n"
            f"4. Pricing/proposal strategy that closed\n\n"
            f"Return JSON: {{\n"
            f'  "skill_name": "short_name",\n'
            f'  "industry": "industry",\n'
            f'  "description": "what this skill does",\n'
            f'  "email_template": "generalized email template with {{placeholders}}",\n'
            f'  "research_focus": ["what to research"],\n'
            f'  "closing_strategy": "how to close",\n'
            f'  "stages_covered": ["email_compose", "close_deal"]\n'
            f"}}",
            model="smart",
            temperature=0.3,
        )

        start = result.find("{")
        end = result.rfind("}") + 1
        return json.loads(result[start:end])

    except Exception as e:
        logger.debug(f"Trajectory generalization failed: {e}")
        return None


async def _write_skill(skill: dict, trajectory: dict) -> Path | None:
    """Write generalized skill as SKILL.md file."""
    try:
        from shared.config import config

        name = skill.get("skill_name", "auto_skill")
        industry = skill.get("industry", "general")
        hash_id = hashlib.md5(f"{name}{industry}{datetime.now().isoformat()}".encode()).hexdigest()[:8]
        skill_dir = config.root_dir / "hermes" / "skills" / f"auto-distilled-{hash_id}"
        skill_dir.mkdir(parents=True, exist_ok=True)

        content = f"""---
name: {name}
description: {skill.get('description', 'Auto-distilled from successful pipeline completion')}
industry: {industry}
stages: {', '.join(skill.get('stages_covered', []))}
auto_distilled: true
distilled_at: {datetime.now().isoformat()}
source_client_id: {trajectory.get('client_id', 'unknown')}
---

# {name}

## When to Use
This skill works best for **{industry}** businesses in the **{trajectory.get('region', 'any')}** region.

## Email Template
{skill.get('email_template', 'No template extracted.')}

## Research Focus
{chr(10).join(f'- {item}' for item in skill.get('research_focus', []))}

## Closing Strategy
{skill.get('closing_strategy', 'Standard closing approach.')}
"""

        (skill_dir / "SKILL.md").write_text(content)
        logger.info(f"Wrote distilled skill: {skill_dir}")
        return skill_dir

    except Exception as e:
        logger.debug(f"Skill write failed: {e}")
        return None


async def get_distilled_skills_for_industry(industry: str) -> list[dict]:
    """Find auto-distilled skills matching an industry."""
    try:
        from shared.config import config
        skills_base = config.root_dir / "hermes" / "skills"
        if not skills_base.exists():
            return []

        matching = []
        for skill_dir in skills_base.iterdir():
            if not skill_dir.name.startswith("auto-distilled-"):
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            content = skill_file.read_text()
            if industry.lower() in content.lower():
                matching.append({
                    "path": str(skill_dir),
                    "name": skill_dir.name,
                    "content_preview": content[:200],
                })
        return matching
    except Exception:
        return []
