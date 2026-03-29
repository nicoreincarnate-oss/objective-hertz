"""
Stage 3: Email Composition
Write 100% custom email per lead using Claude API.
Every email is unique — based on research, personalization hooks, and learnings.
"""
import json
import logging
import re
from pathlib import Path

from shared.anti_slop import (
    AntiSlopScorer,
    detect_secrets,
    record_quality_score,
    rewrite_loop,
)
from shared.anti_slop import (
    is_enabled as anti_slop_enabled,
)
from shared.db import fetch_all, fetch_one
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from shared.skill_loader import execute_skill, find_skill
from titan.memory import compute_prompt_version, get_relevant_learnings
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.email_compose")

# Anti-slop scorer singleton (created once, reused)
_slop_scorer = AntiSlopScorer()


async def _anti_slop_gate(
    subject: str, body: str, lead_id: int | str, rewrite_count_out: list[int] | None = None,
) -> tuple[str, str, bool]:
    """Run anti-slop quality gate on email content.

    Returns (subject, body, passed).
    If anti-slop is disabled, returns original content with passed=True.
    If secrets are detected, returns originals with passed=False (hard block).
    If slop score is too high, runs rewrite loop on the body only.
    Records quality scores to the DB.
    """
    if not anti_slop_enabled():
        return subject, body, True

    combined = f"{subject}\n\n{body}"

    # Secret detection — hard block, never send
    secrets = detect_secrets(combined)
    if secrets:
        logger.error(
            "BLOCKED: secrets detected in email for lead %s: %s",
            lead_id,
            [s["pattern"] for s in secrets],
        )
        await emit_pipeline_error(
            "email_compose.secret_detection",
            ValueError(f"Secrets detected: {[s['pattern'] for s in secrets]}"),
            lead_id=lead_id,
        )
        return subject, body, False

    # Score the body (subject is short; body is the main content)
    scores = await _slop_scorer.score(body, context="email")
    rewrite_count = 0

    # Check threshold — rewrite if needed
    from shared.anti_slop import _composite_score, _is_good_enough

    if not _is_good_enough(scores, "email"):
        logger.info(
            "Email for lead %s below quality threshold (composite=%.2f, slop=%.2f), rewriting",
            lead_id,
            _composite_score(scores),
            scores.get("slop_score", 0),
        )
        body = await rewrite_loop(body, scores, context="email", max_iterations=3)
        # Re-score after rewrite
        scores = await _slop_scorer.score(body, context="email")
        rewrite_count = 1  # At least 1 rewrite attempt

    # Record quality score
    await record_quality_score(
        reference_id=str(lead_id),
        content_type="email",
        scores=scores,
        rewrite_count=rewrite_count,
    )

    if rewrite_count_out is not None:
        rewrite_count_out.append(rewrite_count)

    logger.info(
        "Anti-slop gate for lead %s: composite=%.2f, slop=%.2f, rewrites=%d",
        lead_id,
        _composite_score(scores),
        scores.get("slop_score", 0),
        rewrite_count,
    )

    return subject, body, True


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
             AND email IS NOT NULL AND email != ''
           ORDER BY lead_score DESC, created_at ASC LIMIT %s""",
        (batch_size,),
    )

    # Get relevant learnings from structured DB + vector memory
    learned_tips = await get_relevant_learnings(
        "cold email composition, subject lines, copywriting, what gets replies",
        query_type="email_compose",
    )

    # Get proven rules (deterministic, data-backed constraints)
    from titan.memory import format_rules_for_prompt
    rules_block = await format_rules_for_prompt(["email_performance", "copywriting"])

    soul_copy = _load_soul_copy()

    # Compute prompt version hash for causal attribution (5.1)
    # This traces which prompt config (soul doc + rules + variation) produced each email
    rules_for_hash = await fetch_all(
        """SELECT id, rule_text FROM titan_rules WHERE active = TRUE ORDER BY id"""
    ) if rules_block else []
    prompt_hash = compute_prompt_version(soul_copy, rules_for_hash)

    for lead in leads:
        try:
            if active_skill:
                await _compose_with_skill(lead, active_skill, soul_copy, learned_tips, rules_block, prompt_hash)
            else:
                await _compose_one(lead, soul_copy, learned_tips, rules_block, prompt_hash)
        except Exception as e:
            logger.error(f"Email compose failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("email_compose", e, lead_id=lead["id"])


async def _compose_with_skill(lead: dict, skill_name: str, soul_copy: str, learned_tips: str, rules_block: str = "", prompt_hash: str = ""):
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
{rules_block}

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

    subject = email_data.get("subject", "")
    body = email_data.get("body", "")
    passed, issues = validate_email_content(subject, body)
    if not passed:
        logger.warning(f"Skill email validation failed for lead {lead_id}: {issues}")
        await emit_pipeline_error(
            "email_compose.content_validation",
            ValueError(f"Content issues: {', '.join(issues)}"),
            lead_id=lead_id,
        )
        return

    # Anti-slop quality gate (behind ENABLE_ANTI_SLOP flag)
    subject, body, slop_passed = await _anti_slop_gate(subject, body, lead_id)
    if not slop_passed:
        return

    row = await fetch_one(
        """INSERT INTO email_sequences (client_id, step, subject, body, status, prompt_version_hash)
           VALUES (%s, 1, %s, %s, 'pending', %s)
           ON CONFLICT (client_id, step) DO NOTHING
           RETURNING id""",
        (lead_id, subject, body, prompt_hash),
    )
    if not row:
        logger.debug(f"Email step 1 already exists for lead {lead_id}, skipping")
        return
    await transition_lead(lead_id, "email_drafted")
    logger.info(f"Composed email via skill '{skill_name}' for lead {lead_id} (prompt_v={prompt_hash[:8]})")


async def _compose_one(lead: dict, soul_copy: str, learned_tips: str, rules_block: str = "", prompt_hash: str = ""):
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

{rules_block}

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

    result = await llm.generate(prompt, model=model, temperature=0.8, use_dna=True, daemon_name="titan")

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        email_data = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Failed to parse email JSON for lead {lead_id}")
        return

    # Validate content before storing
    subject = email_data.get("subject", "")
    body = email_data.get("body", "")
    passed, issues = validate_email_content(subject, body)
    if not passed:
        logger.warning(f"Email content validation failed for lead {lead_id}: {issues}")
        await emit_pipeline_error(
            "email_compose.content_validation",
            ValueError(f"Content issues: {', '.join(issues)}"),
            lead_id=lead_id,
        )
        return  # Don't store invalid content

    # Anti-slop quality gate (behind ENABLE_ANTI_SLOP flag)
    subject, body, slop_passed = await _anti_slop_gate(subject, body, lead_id)
    if not slop_passed:
        return

    # Store the email draft — ON CONFLICT prevents duplicate step 1
    row = await fetch_one(
        """INSERT INTO email_sequences (client_id, step, subject, body, status, prompt_version_hash)
           VALUES (%s, 1, %s, %s, 'pending', %s)
           ON CONFLICT (client_id, step) DO NOTHING
           RETURNING id""",
        (lead_id, subject, body, prompt_hash),
    )
    if not row:
        logger.debug(f"Email step 1 already exists for lead {lead_id}, skipping")
        return

    await transition_lead(lead_id, "email_drafted")
    logger.info(f"Composed email for lead {lead_id}: {lead['business_name']} (prompt_v={prompt_hash[:8]})")


def _compose_model_for_lead(lead: dict) -> str:
    """Reserve smart composition for the leads most likely to pay."""
    lead_score = float(lead.get("lead_score", 0) or 0)
    return "smart" if lead_score >= 80 else "fast"


# ── Content validation (GAP 18) ───────────────────────────────────

# Patterns that indicate false or misleading claims the LLM might generate.
_CLAIM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"we.ve (?:helped|worked with|served)\s+\d+", re.IGNORECASE), "fabricated client count"),
    (re.compile(r"\d+\+?\s*(?:businesses|clients|companies)\s+(?:trust|rely on|use)", re.IGNORECASE), "fabricated social proof"),
    (re.compile(r"guarantee(?:d|s)?\s+(?:roi|return|results|revenue|traffic|leads)", re.IGNORECASE), "guaranteed results claim"),
    (re.compile(r"100%\s+(?:satisfaction|money.back|refund)", re.IGNORECASE), "unrealistic guarantee"),
    (re.compile(r"(?:award.winning|certified|accredited)\s+(?:team|agency|company)", re.IGNORECASE), "fabricated credentials"),
    (re.compile(r"as (?:seen|featured) (?:on|in)\s+", re.IGNORECASE), "fabricated media mention"),
]

# Spam trigger words/patterns that hurt deliverability
_SPAM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ACT NOW", re.IGNORECASE), "urgency spam trigger"),
    (re.compile(r"LIMITED TIME", re.IGNORECASE), "urgency spam trigger"),
    (re.compile(r"FREE FREE", re.IGNORECASE), "repeated free"),
    (re.compile(r"!!!", re.IGNORECASE), "excessive punctuation"),
    (re.compile(r"\$\$\$", re.IGNORECASE), "money symbols"),
    (re.compile(r"CLICK HERE", re.IGNORECASE), "click here spam trigger"),
    (re.compile(r"BUY NOW", re.IGNORECASE), "buy now spam trigger"),
]


def validate_email_content(subject: str, body: str) -> tuple[bool, list[str]]:
    """
    Validate generated email content for misleading claims and spam triggers.
    Returns (passed, list_of_issues).
    """
    issues: list[str] = []
    combined = f"{subject} {body}"

    for pattern, reason in _CLAIM_PATTERNS:
        if pattern.search(combined):
            issues.append(f"FALSE_CLAIM: {reason}")

    for pattern, reason in _SPAM_PATTERNS:
        if pattern.search(combined):
            issues.append(f"SPAM: {reason}")

    # Length checks
    if len(subject) > 80:
        issues.append("QUALITY: subject too long (>80 chars)")
    if len(body) > 1000:
        issues.append("QUALITY: body too long (>1000 chars)")
    if len(body) < 30:
        issues.append("QUALITY: body too short (<30 chars)")

    return len(issues) == 0, issues
