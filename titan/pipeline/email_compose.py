"""
Stage 3: Email Composition
Write 100% custom email per lead using Claude API.
Every email is unique — based on research, personalization hooks, and learnings.
"""

import json
import logging
from pathlib import Path

from shared.db import fetch_all, fetch_one, execute
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from shared.skill_loader import find_skill, execute_skill
from titan.state_machine import transition_lead
from titan.memory import get_relevant_learnings

logger = logging.getLogger("perseus.titan.email_compose")

# Skills to try for email composition (in priority order)
EMAIL_SKILLS = [
    "cold-email",               # Hyper-personalized cold email sequences
    "cold-outreach",            # High-converting cold outreach frameworks
    "brw-cold-outreach-sequence",  # Personalized cold outreach sequences
    "outreach-and-prospecting", # Cold and warm outreach campaigns
]

# Load soul/copy guidelines
_SOUL_DIR = Path(__file__).resolve().parent.parent.parent / "soul"


def _load_soul_copy() -> str:
    """Load the copywriter soul file for email writing guidelines."""
    path = _SOUL_DIR / "soul_copy.md"
    if path.exists():
        return path.read_text()
    return "Write professional, concise emails. Lead with benefits, not features."


async def compose_emails(batch_size: int = 20):
    """Compose custom emails for all researched leads. Uses skills if available."""
    # Check for installed email skills
    active_skill = None
    for skill_name in EMAIL_SKILLS:
        if find_skill(skill_name):
            active_skill = skill_name
            logger.info(f"Using skill '{skill_name}' for email composition")
            break

    leads = await fetch_all(
        """SELECT id, business_name, contact_name, email, industry,
                  research_summary, lead_score, language, country, city
           FROM clients WHERE status = 'researched'
           ORDER BY lead_score DESC, created_at ASC LIMIT %s""",
        (batch_size,),
    )

    # Get relevant learnings from structured DB + vector memory
    learned_tips = await get_relevant_learnings(
        "cold email composition, subject lines, copywriting, what gets replies"
    )

    soul_copy = _load_soul_copy()

    for lead in leads:
        try:
            if active_skill:
                await _compose_with_skill(lead, active_skill, soul_copy, learned_tips)
            else:
                await _compose_one(lead, soul_copy, learned_tips)
        except Exception as e:
            logger.error(f"Email compose failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("email_compose", e, lead_id=lead["id"])


async def _compose_with_skill(lead: dict, skill_name: str, soul_copy: str, learned_tips: str):
    """Compose email using an installed skill."""
    lead_id = lead["id"]
    lang = lead.get("language", "en")
    model = _compose_model_for_lead(lead)

    result = await execute_skill(
        skill_name,
        task_prompt=f"""Write a cold outreach email for this lead:

Business: {lead['business_name']}
Contact: {lead.get('contact_name', 'Business Owner')}
Industry: {lead.get('industry', 'unknown')}
Location: {lead.get('city', '')}, {lead.get('country', '')}
Research: {lead.get('research_summary', 'No research available')}
Language: {'Spanish' if lang == 'es' else 'English'}

Offer: Professional website under $325.
Guidelines: {soul_copy[:500]}
What works: {learned_tips[:300]}

Return JSON: {{"subject": "...", "body": "...", "personalization_note": "..."}}""",
        context={"lead_id": str(lead_id)},
        model=model,
    )

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        email_data = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Skill output parse failed for lead {lead_id}, falling back to custom")
        soul_copy_text = _load_soul_copy()
        await _compose_one(lead, soul_copy_text, learned_tips)
        return

    await fetch_one(
        """INSERT INTO email_sequences (client_id, step, subject, body, status)
           VALUES (%s, 1, %s, %s, 'pending') RETURNING id""",
        (lead_id, email_data.get("subject", ""), email_data.get("body", "")),
    )
    await transition_lead(lead_id, "email_drafted")
    logger.info(f"Composed email via skill '{skill_name}' for lead {lead_id}")


async def _compose_one(lead: dict, soul_copy: str, learned_tips: str):
    """Compose a custom email for one lead."""
    lead_id = lead["id"]
    lang = lead.get("language", "en")
    lang_instruction = f"Write in {'Spanish' if lang == 'es' else 'English'}."
    model = _compose_model_for_lead(lead)

    prompt = f"""You are Titan's email copywriter. Write a cold outreach email.

COPYWRITING GUIDELINES:
{soul_copy}

WHAT WE'VE LEARNED WORKS:
{learned_tips}

LEAD INFORMATION:
- Business: {lead['business_name']}
- Contact: {lead.get('contact_name', 'Business Owner')}
- Industry: {lead.get('industry', 'unknown')}
- Location: {lead.get('city', '')}, {lead.get('country', '')}
- Research: {lead.get('research_summary', 'No research available')}

REQUIREMENTS:
- {lang_instruction}
- 100% unique — never a template
- Short (under 150 words)
- Personalized to their specific business
- Clear value proposition (professional website under $325)
- One clear CTA (reply to discuss)
- No spam triggers, no ALL CAPS, no excessive punctuation
- Subject line under 50 characters

Return JSON:
{{
    "subject": "...",
    "body": "...",
    "personalization_note": "why this email is unique to them"
}}"""

    result = await llm.generate(prompt, model=model, temperature=0.8)

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        email_data = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Failed to parse email JSON for lead {lead_id}")
        return

    # Store the email draft
    await fetch_one(
        """INSERT INTO email_sequences (client_id, step, subject, body, status)
           VALUES (%s, 1, %s, %s, 'pending') RETURNING id""",
        (lead_id, email_data.get("subject", ""), email_data.get("body", "")),
    )

    await transition_lead(lead_id, "email_drafted")
    logger.info(f"Composed email for lead {lead_id}: {lead['business_name']}")


def _compose_model_for_lead(lead: dict) -> str:
    """Always use Claude for client-facing emails — it sounds human.
    Sonnet for high-value leads, Haiku for the rest (still Claude, still human-sounding)."""
    lead_score = float(lead.get("lead_score", 0) or 0)
    return "smart" if lead_score >= 70 else "fast-remote"
