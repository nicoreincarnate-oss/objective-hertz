"""
Stage 3: Email Composition
Write 100% custom email per lead using Claude API.
Every email is unique — based on research, personalization hooks, and learnings.

RLM Integration (Phase 5):
  - RLM_ENABLED + RLM_SHADOW_MODE → run both paths, log comparison, return original
  - RLM_ENABLED + no shadow → full RLM compose (recursive draft-evaluate-refine)
  - RLM disabled → original single-pass compose
  Feature flags checked from system_config (runtime toggle) with env var fallback.
"""
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from shared.anti_slop import (
    AntiSlopScorer,
    _composite_score,
    detect_secrets,
    record_quality_score,
    rewrite_loop,
)
from shared.anti_slop import (
    is_enabled as anti_slop_enabled,
)
from shared.db import fetch_all, fetch_one, get_config
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from shared.skill_loader import execute_skill, find_skill
from titan.memory import compute_prompt_version, get_relevant_learnings
from titan.pipeline.rlm_composer import RLMComposer
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.email_compose")

# Anti-slop scorer singleton (created once, reused)
_slop_scorer = AntiSlopScorer()

# RLM composer singleton (lazy init)
_rlm_composer: RLMComposer | None = None


def _get_rlm_composer() -> RLMComposer:
    """Get or create RLM composer singleton."""
    global _rlm_composer
    if _rlm_composer is None:
        _rlm_composer = RLMComposer()
    return _rlm_composer


# ---------------------------------------------------------------------------
# RLM Feature Flags — system_config (runtime) with env var fallback
# ---------------------------------------------------------------------------

async def is_rlm_enabled() -> bool:
    """Check if RLM compose is enabled.

    Checks system_config table first (runtime toggle via dashboard),
    falls back to ENABLE_RLM env var.
    """
    try:
        db_val = await get_config("rlm_enabled")
        if db_val is not None:
            if isinstance(db_val, bool):
                return db_val
            return str(db_val).lower() in ("true", "1")
    except Exception:
        pass  # DB unavailable, fall back to env
    return os.environ.get("ENABLE_RLM", "").lower() in ("true", "1")


async def is_rlm_shadow_mode() -> bool:
    """Check if RLM is in shadow (A/B comparison) mode.

    Shadow mode: run both original + RLM, log comparison, return original.
    Checks system_config first, falls back to RLM_SHADOW_MODE env var.
    """
    try:
        db_val = await get_config("rlm_shadow_mode")
        if db_val is not None:
            if isinstance(db_val, bool):
                return db_val
            return str(db_val).lower() in ("true", "1")
    except Exception:
        pass
    return os.environ.get("RLM_SHADOW_MODE", "").lower() in ("true", "1")


async def _log_ab_comparison(
    lead_id: int | str,
    original_subject: str,
    original_body: str,
    rlm_result: dict[str, Any],
) -> None:
    """Log A/B comparison data between original and RLM compose paths.

    Stores comparison in events table for analysis during shadow period.
    """
    # Score the original for fair comparison
    original_scores = await _slop_scorer.score(
        f"{original_subject}\n\n{original_body}", context="email"
    )
    original_composite = _composite_score(original_scores)

    rlm_composite = rlm_result.get("composite", 0.0)
    rlm_scores = rlm_result.get("scores", {})
    improvement = rlm_composite - original_composite if original_composite > 0 else 0.0

    comparison = {
        "lead_id": str(lead_id),
        "timestamp": time.time(),
        "original": {
            "composite": round(original_composite, 4),
            "scores": {k: round(v, 4) for k, v in original_scores.items()},
        },
        "rlm": {
            "composite": round(rlm_composite, 4),
            "scores": {k: round(v, 4) for k, v in rlm_scores.items()},
            "iterations": rlm_result.get("iterations", 0),
            "budget_used": round(rlm_result.get("budget_used", 0.0), 4),
        },
        "improvement": round(improvement, 4),
        "rlm_won": rlm_composite > original_composite,
    }

    logger.info(
        "RLM A/B comparison for lead %s: original=%.3f, rlm=%.3f, improvement=%.3f, rlm_won=%s",
        lead_id,
        original_composite,
        rlm_composite,
        improvement,
        rlm_composite > original_composite,
    )

    # Store in events for dashboard analysis
    try:
        from shared.db import emit_event
        await emit_event("rlm_ab_comparison", comparison)
    except Exception as exc:
        logger.warning("Failed to store RLM A/B comparison event: %s", exc)


async def _compose_with_rlm(
    lead: dict,
    soul_copy: str,
    learned_tips: str,
    rules_block: str = "",
    prompt_hash: str = "",
) -> bool:
    """Compose email using RLM recursive composer.

    Handles both shadow mode (A/B comparison) and full RLM mode.
    Returns True if email was composed and stored, False otherwise.
    """
    lead_id = lead["id"]
    shadow = await is_rlm_shadow_mode()

    # Build research dict from lead data for RLM
    research: dict[str, Any] = {
        "research_summary": lead.get("research_summary", ""),
        "industry": lead.get("industry", ""),
        "business_name": lead.get("business_name", ""),
        "city": lead.get("city", ""),
        "country": lead.get("country", ""),
    }

    composer = _get_rlm_composer()

    if shadow:
        # Shadow mode: run BOTH paths, log comparison, return original
        logger.info("RLM shadow mode: running both paths for lead %s", lead_id)

        # Run original compose (don't store yet)
        original_subject, original_body = await _original_compose_draft(
            lead, soul_copy, learned_tips, rules_block
        )

        if not original_body:
            logger.warning("Original compose failed for lead %s in shadow mode", lead_id)
            return False

        # Run RLM compose (for comparison only)
        try:
            rlm_result = await composer.compose(lead, research)
        except Exception as exc:
            logger.warning("RLM compose failed in shadow mode for lead %s: %s", lead_id, exc)
            rlm_result = {"composite": 0.0, "scores": {}, "iterations": 0, "budget_used": 0.0}

        # Log the comparison
        await _log_ab_comparison(lead_id, original_subject, original_body, rlm_result)

        # Store the ORIGINAL (shadow = safe)
        subject, body = original_subject, original_body

    else:
        # Full RLM mode
        logger.info("RLM full mode: recursive compose for lead %s", lead_id)
        try:
            rlm_result = await composer.compose(lead, research)
        except Exception as exc:
            logger.error("RLM compose failed for lead %s: %s", lead_id, exc)
            return False

        if rlm_result.get("budget_exceeded"):
            logger.warning(
                "RLM budget exceeded for lead %s: %s — falling back to single-pass",
                lead_id, rlm_result.get("reason", "unknown"),
            )
            return False  # Caller will fall back to _compose_one

        subject = rlm_result.get("subject", "")
        body = rlm_result.get("body", "")

        if not body:
            logger.warning("RLM produced empty body for lead %s", lead_id)
            return False

        logger.info(
            "RLM compose for lead %s: composite=%.3f, iterations=%d, budget=$%.4f",
            lead_id,
            rlm_result.get("composite", 0),
            rlm_result.get("iterations", 0),
            rlm_result.get("budget_used", 0),
        )

    # Validate content
    passed, issues = validate_email_content(subject, body)
    if not passed:
        logger.warning("RLM email validation failed for lead %s: %s", lead_id, issues)
        await emit_pipeline_error(
            "email_compose.content_validation",
            ValueError(f"Content issues: {', '.join(issues)}"),
            lead_id=lead_id,
        )
        return False

    # Anti-slop gate
    subject, body, slop_passed = await _anti_slop_gate(subject, body, lead_id)
    if not slop_passed:
        return False

    # Store the email draft
    compose_method = "rlm_shadow" if shadow else "rlm"
    row = await fetch_one(
        """INSERT INTO email_sequences (client_id, step, subject, body, status, prompt_version_hash)
           VALUES (%s, 1, %s, %s, 'pending', %s)
           ON CONFLICT (client_id, step) DO NOTHING
           RETURNING id""",
        (lead_id, subject, body, prompt_hash),
    )
    if not row:
        logger.debug("Email step 1 already exists for lead %s, skipping", lead_id)
        return True  # Already drafted, not an error

    await transition_lead(lead_id, "email_drafted")
    logger.info(
        "Composed email via %s for lead %s (prompt_v=%s)",
        compose_method, lead_id, prompt_hash[:8] if prompt_hash else "none",
    )
    return True


async def _original_compose_draft(
    lead: dict,
    soul_copy: str,
    learned_tips: str,
    rules_block: str = "",
) -> tuple[str, str]:
    """Generate an email draft using the original single-pass method.

    Returns (subject, body) tuple. Used by shadow mode for comparison.
    Does NOT store the email — caller decides what to store.
    """
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
        return "", ""

    return email_data.get("subject", ""), email_data.get("body", "")


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

    # Check RLM feature flag once per batch (not per lead)
    rlm_enabled = await is_rlm_enabled()
    if rlm_enabled:
        logger.info("RLM compose enabled — shadow=%s", await is_rlm_shadow_mode())

    for lead in leads:
        try:
            # RLM path takes priority over skill path when enabled
            if rlm_enabled:
                success = await _compose_with_rlm(
                    lead, soul_copy, learned_tips, rules_block, prompt_hash
                )
                if success:
                    continue
                # RLM failed or budget exceeded — fall through to original path
                logger.info("RLM fallback to original compose for lead %s", lead["id"])

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
