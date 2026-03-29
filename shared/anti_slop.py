"""Anti-slop quality gate: 5-dimension scorer, rewrite loop, secret detection, DB functions.

Implements the SlopScorer Protocol from shared.contracts.
Feature flag: ENABLE_ANTI_SLOP env var (true/1 to enable).

Slop score is regex-based (no LLM). Other 4 dimensions use Haiku via llm_client.
Secret detection is pure regex, no LLM.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta

logger = logging.getLogger("anti_slop")

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Check whether the anti-slop quality gate is active."""
    return os.environ.get("ENABLE_ANTI_SLOP", "").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# Slop patterns (60+ overused AI phrases)
# ---------------------------------------------------------------------------

SLOP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # Greetings / openers
        r"I hope this (?:email|message) finds you well",
        r"I wanted to reach out",
        r"Just circling back",
        r"I'm reaching out because",
        r"Hope you're doing well",
        r"Thank you for your time",
        # Filler / vague claims
        r"\beverything you need\b",
        r"\bunlock the full potential\b",
        r"\btake (?:it |things )?to the next level\b",
        r"\bin today'?s (?:fast[- ]paced|competitive|digital|modern)\b",
        r"\bleverage\b",
        r"\bsynergy\b",
        r"\bgame[- ]?changer\b",
        r"\bcutting[- ]?edge\b",
        r"\bstate[- ]?of[- ]?the[- ]?art\b",
        r"\bdeep dive\b",
        r"\blow[- ]?hanging fruit\b",
        r"\bmove the needle\b",
        r"\bat the end of the day\b",
        r"\btouch base\b",
        r"\brevolutionary\b",
        r"\btransformative\b",
        r"\bparadigm shift\b",
        r"\bseamless(?:ly)?\b",
        r"\brobust\b",
        r"\bscalable\b",
        r"\binnovative\b",
        r"\bworld[- ]?class\b",
        r"\bnext[- ]?gen(?:eration)?\b",
        r"\bholistic\b",
        r"\bsynergize\b",
        r"\bstreamline\b",
        r"\bempower(?:ing|s|ed)?\b",
        r"\boptimize\b",
        r"\bfacilitate\b",
        r"\bactionable\b",
        r"\bimpactful\b",
        r"\bproactive(?:ly)?\b",
        r"\bbest[- ]?in[- ]?class\b",
        r"\bthought leader(?:ship)?\b",
        r"\bvalue[- ]?add(?:ed)?\b",
        r"\bbandwidth\b",
        r"\bpivot\b",
        r"\bdisrupt(?:ive|or|ion)?\b",
        r"\becosystem\b",
        r"\bsynergistic\b",
        r"\bfuture[- ]?proof\b",
        r"\bneedle[- ]?moving\b",
        r"\bpain point\b",
        r"\bsolution(?:s)? (?:that |which )?(?:drives?|delivers?|enables?)\b",
        r"\bturn[- ]?key\b",
        r"\bmission[- ]?critical\b",
        r"\bgranular\b",
        r"\bvirtuous cycle\b",
        r"\bforce multiplier\b",
        r"\bone[- ]?stop[- ]?shop\b",
        r"\bI'd love to\b",
        r"\bLet me know if you have any questions\b",
        r"\bPlease don'?t hesitate to\b",
        r"\bLooking forward to (?:hearing|connecting)\b",
        r"\bBest regards\b",
        r"\bSincerely\b",
        r"\bexcited to share\b",
        r"\bthrilled to announce\b",
        r"\bdelighted to\b",
        r"\bWe are pleased to\b",
    ]
]


# ---------------------------------------------------------------------------
# Secret detection patterns (25+)
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, flags), desc)
    for p, desc, flags in [
        # API keys
        (r"sk-[a-zA-Z0-9]{20,}", "API key (OpenAI/Anthropic format)", 0),
        (r"sk-ant-[a-zA-Z0-9\-]{20,}", "Anthropic API key", 0),
        (r"ghp_[a-zA-Z0-9]{36}", "GitHub personal access token", 0),
        (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth token", 0),
        (r"github_pat_[a-zA-Z0-9_]{22,}", "GitHub fine-grained PAT", 0),
        (r"xoxb-[0-9]+-[a-zA-Z0-9]+", "Slack bot token", 0),
        (r"xoxp-[0-9]+-[a-zA-Z0-9]+", "Slack user token", 0),
        (r"xoxs-[0-9]+-[a-zA-Z0-9]+", "Slack session token", 0),
        (r"AKIA[0-9A-Z]{16}", "AWS access key ID", 0),
        (r"[A-Z0-9]{20}:[A-Za-z0-9+/=]{40,}", "AWS-style credential pair", 0),
        (r"(?:bearer|token)\s+[a-zA-Z0-9\-_.]{20,}", "Bearer/token auth header", re.IGNORECASE),
        (r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", "JWT token", 0),
        # Cloud / SaaS keys
        (r"AIza[0-9A-Za-z\-_]{35}", "Google API key", 0),
        (r"ya29\.[a-zA-Z0-9_-]{50,}", "Google OAuth token", 0),
        (r"sk_live_[a-zA-Z0-9]{24,}", "Stripe live secret key", 0),
        (r"sk_test_[a-zA-Z0-9]{24,}", "Stripe test secret key", 0),
        (r"rk_live_[a-zA-Z0-9]{24,}", "Stripe restricted key", 0),
        (r"sq0[a-z]{3}-[a-zA-Z0-9\-_]{22,}", "Square credential", 0),
        (r"SG\.[a-zA-Z0-9\-_]{22,}\.[a-zA-Z0-9\-_]{22,}", "SendGrid API key", 0),
        # Passwords / secrets in config
        (r"(?:password|passwd|pwd)\s*[:=]\s*\S+", "Password in plaintext", re.IGNORECASE),
        (r"(?:secret|api_key|apikey|access_token)\s*[:=]\s*['\"]?\S{8,}", "Secret/key assignment", re.IGNORECASE),
        # PII
        (r"\b\d{3}-\d{2}-\d{4}\b", "US Social Security Number", 0),
        (r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", "Credit card number", 0),
        # Private keys
        (r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "Private key block", 0),
        (r"-----BEGIN PGP PRIVATE KEY BLOCK-----", "PGP private key", 0),
        # Connection strings
        (r"(?:mysql|postgres|postgresql|mongodb|redis)://\S+:\S+@\S+", "Database connection string with credentials", re.IGNORECASE),
    ]
]


# ---------------------------------------------------------------------------
# Thresholds per content type
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, dict[str, float]] = {
    "email":     {"min_composite": 0.7,  "max_slop": 0.2},
    "site_copy": {"min_composite": 0.75, "max_slop": 0.15},
    "alert":     {"min_composite": 0.5,  "max_slop": 0.4},
    "internal":  {"min_composite": 0.3,  "max_slop": 0.6},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _composite_score(scores: dict[str, float]) -> float:
    """Weighted average of all 5 dimensions. Slop is inverted (lower is better)."""
    clarity = scores.get("clarity", 0.0)
    specificity = scores.get("specificity", 0.0)
    authenticity = scores.get("authenticity", 0.0)
    value_density = scores.get("value_density", 0.0)
    slop_raw = scores.get("slop_score", 1.0)
    # Invert slop: 0 slop -> 1.0 contribution, 1.0 slop -> 0.0
    slop_inverted = 1.0 - slop_raw
    # Equal weights across 5 dimensions
    return (clarity + specificity + authenticity + value_density + slop_inverted) / 5.0


def _is_good_enough(scores: dict[str, float], context: str) -> bool:
    """Check if scores meet the good-enough threshold for this context."""
    t = THRESHOLDS.get(context, THRESHOLDS["email"])
    composite = _composite_score(scores)
    return composite >= t["min_composite"] and scores.get("slop_score", 1.0) <= t["max_slop"]


def _calculate_slop_score(content: str) -> float:
    """Regex-based slop score: count pattern matches, normalize to 0-1."""
    if not content or not content.strip():
        return 0.0
    total_matches = 0
    for pattern in SLOP_PATTERNS:
        total_matches += len(pattern.findall(content))
    # Normalize: 0 matches = 0.0 (no slop), 10+ matches = 1.0 (max slop)
    # Use a sigmoid-like curve: score = min(1.0, matches / 10)
    word_count = max(len(content.split()), 1)
    # Density-based: matches per 100 words, capped at 1.0
    density = (total_matches / word_count) * 100
    return min(1.0, density / 10.0)


_SCORING_RUBRIC = """\
You are a content quality evaluator. Score the following content on 4 dimensions.
Each score is a float from 0.0 (worst) to 1.0 (best).

Dimensions:
- clarity: Is the writing clear, well-structured, easy to understand?
- specificity: Does it reference concrete details, numbers, names? (vs vague claims)
- authenticity: Does it sound like a real human wrote it? (vs generic AI boilerplate)
- value_density: Does every sentence add value? (vs filler and padding)

Content type: {context}

Return ONLY a JSON object with exactly these 4 keys and float values.
Example: {{"clarity": 0.8, "specificity": 0.6, "authenticity": 0.7, "value_density": 0.5}}

Content to score:
---
{content}
---
"""


async def _llm_score_dimensions(content: str, context: str) -> dict[str, float]:
    """Use Haiku to score 4 non-slop dimensions. Returns defaults on failure."""
    defaults = {"clarity": 0.5, "specificity": 0.5, "authenticity": 0.5, "value_density": 0.5}
    try:
        from shared.llm_client import llm

        prompt = _SCORING_RUBRIC.format(content=content[:2000], context=context)
        raw = await llm.generate(
            prompt,
            model="fast",
            max_tokens=200,
            temperature=0.0,
        )
        # Parse JSON from response
        # Try to extract JSON from the response even if there's surrounding text
        match = re.search(r"\{[^}]+\}", raw)
        if match:
            parsed = json.loads(match.group())
            result = {}
            for key in ("clarity", "specificity", "authenticity", "value_density"):
                val = parsed.get(key, 0.5)
                result[key] = max(0.0, min(1.0, float(val)))
            return result
    except Exception as e:
        logger.warning("LLM scoring failed, using defaults: %s", e)
    return defaults


# ---------------------------------------------------------------------------
# AntiSlopScorer (implements SlopScorer Protocol)
# ---------------------------------------------------------------------------

class AntiSlopScorer:
    """Scores content quality across 5 dimensions. Implements SlopScorer Protocol."""

    DIMENSIONS = ["clarity", "specificity", "authenticity", "value_density", "slop_score"]

    async def score(self, content: str, context: str = "email") -> dict[str, float]:
        """Score content on 5 dimensions.

        - slop_score: regex-based, no LLM (0 = no slop, 1 = max slop)
        - clarity, specificity, authenticity, value_density: Haiku LLM evaluation
        """
        slop = _calculate_slop_score(content)
        llm_scores = await _llm_score_dimensions(content, context)
        return {
            "clarity": llm_scores["clarity"],
            "specificity": llm_scores["specificity"],
            "authenticity": llm_scores["authenticity"],
            "value_density": llm_scores["value_density"],
            "slop_score": slop,
        }

    def get_threshold(self, context: str) -> float:
        """Return the minimum acceptable composite score for this context type."""
        t = THRESHOLDS.get(context, THRESHOLDS["email"])
        return t["min_composite"]


# ---------------------------------------------------------------------------
# Secret detection
# ---------------------------------------------------------------------------

def detect_secrets(content: str) -> list[dict[str, str | int]]:
    """Scan content for leaked secrets/PII. Returns list of findings.

    Pure regex, no LLM. Intended to run on ALL outbound content.
    If findings are returned, the content MUST be blocked from sending.
    """
    findings: list[dict[str, str | int]] = []
    for pattern, description in SECRET_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            findings.append({"pattern": description, "count": len(matches)})
    return findings


# ---------------------------------------------------------------------------
# Rewrite loop (best-of-N)
# ---------------------------------------------------------------------------

_REWRITE_PROMPT = """\
Rewrite the following {context} content to improve quality.

Issues to fix:
{issues}

Original content:
---
{content}
---

Write ONLY the improved version, no commentary.
"""


def _format_issues(scores: dict[str, float]) -> str:
    """Format low-scoring dimensions into actionable feedback."""
    issues = []
    if scores.get("slop_score", 0) > 0.2:
        issues.append("Too many cliches and generic phrases. Use specific, concrete language.")
    if scores.get("clarity", 1) < 0.7:
        issues.append("Writing is unclear. Simplify sentence structure.")
    if scores.get("specificity", 1) < 0.7:
        issues.append("Too vague. Add specific details, numbers, or names.")
    if scores.get("authenticity", 1) < 0.7:
        issues.append("Sounds like generic AI output. Make it sound human and natural.")
    if scores.get("value_density", 1) < 0.7:
        issues.append("Too much filler. Every sentence should add value.")
    return "\n".join(issues) if issues else "General quality improvement needed."


async def rewrite_loop(
    content: str,
    initial_scores: dict[str, float],
    context: str = "email",
    max_iterations: int = 3,
) -> str:
    """Rewrite content up to max_iterations times. Returns highest-scoring version."""
    scorer = AntiSlopScorer()
    versions: list[tuple[str, dict[str, float]]] = [(content, initial_scores)]

    for _ in range(max_iterations):
        current_content = versions[-1][0]
        current_scores = versions[-1][1]

        # Check if already good enough
        if _is_good_enough(current_scores, context):
            break

        try:
            from shared.llm_client import llm

            prompt = _REWRITE_PROMPT.format(
                context=context,
                issues=_format_issues(current_scores),
                content=current_content[:3000],
            )
            rewrite = await llm.generate(prompt, model="fast", max_tokens=2048, temperature=0.7)
            if rewrite and rewrite.strip():
                new_scores = await scorer.score(rewrite.strip(), context)
                versions.append((rewrite.strip(), new_scores))
        except Exception as e:
            logger.warning("Rewrite iteration failed: %s", e)
            break

    # Return highest-scoring version (not necessarily latest)
    return max(versions, key=lambda v: _composite_score(v[1]))[0]


# ---------------------------------------------------------------------------
# DB functions (quality_scores table)
# ---------------------------------------------------------------------------

async def record_quality_score(
    reference_id: str,
    content_type: str,
    scores: dict[str, float],
    rewrite_count: int = 0,
) -> None:
    """Insert a quality score record into the quality_scores table."""
    try:
        from shared.db import execute

        composite = _composite_score(scores)
        await execute(
            """INSERT INTO quality_scores
               (content_type, reference_id, clarity, specificity, authenticity,
                value_density, slop_score, composite, rewrite_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                content_type,
                reference_id,
                round(scores.get("clarity", 0.0), 3),
                round(scores.get("specificity", 0.0), 3),
                round(scores.get("authenticity", 0.0), 3),
                round(scores.get("value_density", 0.0), 3),
                round(scores.get("slop_score", 0.0), 3),
                round(composite, 3),
                rewrite_count,
            ),
        )
    except Exception as e:
        logger.warning("Failed to record quality score: %s", e)


async def get_quality_trend(
    content_type: str,
    days: int = 30,
) -> list[dict]:
    """Get average quality scores per day for trend analysis.

    Returns list of dicts with date + avg scores, ordered by date ascending.
    """
    try:
        from shared.db import fetch_all

        since = date.today() - timedelta(days=days)
        rows = await fetch_all(
            """SELECT
                   DATE(created_at) AS day,
                   AVG(clarity)       AS avg_clarity,
                   AVG(specificity)   AS avg_specificity,
                   AVG(authenticity)   AS avg_authenticity,
                   AVG(value_density)  AS avg_value_density,
                   AVG(slop_score)     AS avg_slop,
                   AVG(composite)      AS avg_composite,
                   COUNT(*)            AS sample_count
               FROM quality_scores
               WHERE content_type = %s AND created_at >= %s
               GROUP BY DATE(created_at)
               ORDER BY DATE(created_at) ASC""",
            (content_type, since),
        )
        return rows
    except Exception as e:
        logger.warning("Failed to fetch quality trend: %s", e)
        return []
