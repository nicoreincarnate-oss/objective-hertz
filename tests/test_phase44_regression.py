"""Phase 44 — Golden prompt regression tests.

Locks down expected output characteristics for ~20 known-good prompts across the
critical pipeline stages. Run these on EVERY backend (old direct-Anthropic, new
LiteLLM proxy, and after every model upgrade) to catch silent regressions.

These are NOT exact-match tests — language model outputs vary. They're structural
+ semantic invariants:
- Length range (e.g. cold email body must be 200-500 chars)
- Required tokens (e.g. mentions the lead's company name)
- Forbidden tokens (e.g. no "Lorem ipsum", no AI tells)
- Schema validity (e.g. JSON parses + matches expected fields)

Run:
    pytest tests/test_phase44_regression.py -v
    pytest tests/test_phase44_regression.py -v --backend=litellm
    pytest tests/test_phase44_regression.py -v --backend=anthropic_direct
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class GoldenPrompt:
    name: str
    prompt: str
    operation: str
    daemon: str
    tier: str
    expected_length_range: tuple[int, int]
    must_contain: list[str] = field(default_factory=list)
    must_contain_any: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    must_match_regex: list[str] = field(default_factory=list)
    must_be_json: bool = False
    expected_json_keys: list[str] = field(default_factory=list)
    max_latency_ms: int = 30_000


GOLDEN_PROMPTS: list[GoldenPrompt] = [
    # ─── Titan email composition ────────────────────────────────────────
    GoldenPrompt(
        name="titan_email_compose_dentist",
        prompt=(
            "Write a personalized cold email to Dr. Smith at Smith Dental in Austin, TX. "
            "They have no website. Pitch our website-build service. "
            "Tone: warm, specific, under 150 words."
        ),
        operation="email_compose",
        daemon="titan",
        tier="smart",
        expected_length_range=(200, 1200),
        must_contain_any=["Smith", "dental", "Austin"],
        must_not_contain=[
            "lorem ipsum", "placeholder", "TODO", "[YOUR NAME]",
            "delve", "tapestry", "I cannot",
        ],
    ),

    GoldenPrompt(
        name="titan_lead_classification",
        prompt=(
            "Classify this business as good fit (true/false) for our website service. "
            "Reasoning in 1 sentence. Return JSON: {fit: bool, reason: str}.\n\n"
            "Business: Joe's Plumbing, no website, found via Google Maps."
        ),
        operation="lead_classification",
        daemon="titan",
        tier="local",
        expected_length_range=(20, 500),
        must_be_json=True,
        expected_json_keys=["fit", "reason"],
    ),

    # ─── Hermes briefings ───────────────────────────────────────────────
    GoldenPrompt(
        name="hermes_morning_briefing",
        prompt=(
            "Generate a one-paragraph morning briefing for the operator. "
            "Include: weather (sunny 72F), 3 calendar events, pipeline status (5 hot leads), "
            "and one suggested first action. Friendly tone, under 200 words."
        ),
        operation="briefing_generation",
        daemon="hermes",
        tier="local",
        expected_length_range=(100, 1500),
        must_contain_any=["sunny", "72", "leads"],
        must_not_contain=["lorem ipsum", "TODO", "[OPERATOR]"],
    ),

    GoldenPrompt(
        name="hermes_alert_classification",
        prompt=(
            "Classify this event into one of: critical, warning, info. "
            "Return JSON: {level: str, summary: str (1 sentence)}\n\n"
            "Event: Domain bounce rate spiked to 12% on send-domain-3.com over the last hour."
        ),
        operation="alert_classification",
        daemon="hermes",
        tier="local",
        expected_length_range=(20, 400),
        must_be_json=True,
        expected_json_keys=["level", "summary"],
    ),

    # ─── Clawdbot site building ─────────────────────────────────────────
    GoldenPrompt(
        name="clawdbot_section_planner",
        prompt=(
            "Plan a 5-section landing page for a dentist office. "
            "Return JSON: {sections: [{type: str, headline: str, purpose: str}]}"
        ),
        operation="section_planner",
        daemon="clawdbot",
        tier="smart",
        expected_length_range=(100, 3000),
        must_be_json=True,
        expected_json_keys=["sections"],
    ),

    # ─── Ruflo code fixing (Aider architect call) ───────────────────────
    GoldenPrompt(
        name="ruflo_aider_architect_simple_bug",
        prompt=(
            "Plan a fix for this failing test:\n\n"
            "test_user_age_calculation: expected 25, got 24\n"
            "File: shared/utils.py, function: calculate_age(birth_date)\n\n"
            "Return JSON: {root_cause: str, files_to_change: [str], "
            "test_to_add: str, plan_steps: [str]}"
        ),
        operation="aider_architect",
        daemon="ruflo",
        tier="smart",
        expected_length_range=(50, 2000),
        must_be_json=True,
        expected_json_keys=["root_cause", "files_to_change", "plan_steps"],
    ),

    # ─── Deerflow research ──────────────────────────────────────────────
    GoldenPrompt(
        name="deerflow_source_scoring",
        prompt=(
            "Score this source for adoption (0.0-1.0). Return JSON.\n\n"
            "Title: 'New Python Library for HTTP/3'\n"
            "URL: https://github.com/example/h3lib\n"
            "Stars: 412, Last commit: 3 days ago, Language: Python"
        ),
        operation="source_scoring",
        daemon="deerflow",
        tier="local",
        expected_length_range=(20, 500),
        must_be_json=True,
    ),

    # ─── Conway ledger explanation ──────────────────────────────────────
    GoldenPrompt(
        name="conway_ledger_explanation",
        prompt=(
            "Explain this ledger entry in 1 sentence for the operator:\n"
            "Agent: titan, type: spend, amount: 0.25 USDC, "
            "counterparty: openrouter, description: claude-sonnet-4-6 inference."
        ),
        operation="ledger_explanation",
        daemon="conway",
        tier="local",
        expected_length_range=(20, 400),
        must_not_contain=["sk-", "api_key", "password"],
    ),

    # ─── Openjarvis workflow synthesis ──────────────────────────────────
    GoldenPrompt(
        name="openjarvis_dag_node_label",
        prompt=(
            "Generate a 3-5 word label for this DAG node:\n"
            "Node type: tool, action: scrape_website, inputs: {url: str}, "
            "outputs: {html: str, status: int}"
        ),
        operation="dag_node_label",
        daemon="openjarvis",
        tier="local",
        expected_length_range=(3, 100),
    ),
]


# ============================================================================
# Test runner
# ============================================================================

@pytest.fixture(scope="session")
def backend_name() -> str:
    return os.environ.get("REGRESSION_BACKEND", "anthropic_direct")


@pytest.fixture(scope="session")
def llm_client():
    """Real LLM client. Configure via REGRESSION_BACKEND env var."""
    try:
        from shared.llm_client import LLMClient
        return LLMClient()
    except ImportError:
        pytest.skip("shared.llm_client not importable in test env")


@pytest.mark.parametrize("golden", GOLDEN_PROMPTS, ids=lambda g: g.name)
@pytest.mark.asyncio
async def test_golden_prompt(golden: GoldenPrompt, llm_client, backend_name):
    """Run a golden prompt and verify all invariants hold."""
    if os.environ.get("SKIP_LIVE_LLM_TESTS"):
        pytest.skip("Live LLM tests disabled (SKIP_LIVE_LLM_TESTS set)")

    response = await llm_client.generate(
        golden.prompt,
        model=golden.tier,
        daemon_name=golden.daemon,
        pipeline_stage=golden.operation,
        max_tokens=2000,
    )

    assert isinstance(response, str), f"Expected str, got {type(response)}"
    assert response.strip(), "Empty response"

    response_lower = response.lower()
    response_length = len(response)
    min_len, max_len = golden.expected_length_range
    assert min_len <= response_length <= max_len, (
        f"{golden.name}: length {response_length} outside [{min_len}, {max_len}]"
    )

    for must_have in golden.must_contain:
        assert must_have.lower() in response_lower, (
            f"{golden.name}: missing required token '{must_have}'"
        )

    if golden.must_contain_any:
        assert any(t.lower() in response_lower for t in golden.must_contain_any), (
            f"{golden.name}: must contain at least one of {golden.must_contain_any}"
        )

    for forbidden in golden.must_not_contain:
        assert forbidden.lower() not in response_lower, (
            f"{golden.name}: forbidden token '{forbidden}' present"
        )

    for pattern in golden.must_match_regex:
        assert re.search(pattern, response, re.IGNORECASE), (
            f"{golden.name}: must match regex {pattern}"
        )

    if golden.must_be_json:
        json_text = response
        # Extract first JSON object if response has surrounding prose
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if m:
            json_text = m.group(0)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{golden.name}: JSON parse failed: {exc}\nResponse: {response[:300]}")
        assert isinstance(data, dict), f"{golden.name}: expected JSON object"
        for key in golden.expected_json_keys:
            assert key in data, f"{golden.name}: missing required JSON key '{key}'"


def test_all_golden_prompts_have_unique_names():
    names = [g.name for g in GOLDEN_PROMPTS]
    assert len(names) == len(set(names)), "Duplicate golden prompt names"


def test_all_golden_prompts_have_valid_tier():
    from shared.tiers import TierName
    valid = {t.value for t in TierName}
    for g in GOLDEN_PROMPTS:
        assert g.tier in valid, f"{g.name}: invalid tier '{g.tier}'"


def test_at_least_one_golden_per_critical_daemon():
    critical = {"titan", "clawdbot", "ruflo", "hermes"}
    daemons_covered = {g.daemon for g in GOLDEN_PROMPTS}
    missing = critical - daemons_covered
    assert not missing, f"Missing golden prompts for critical daemons: {missing}"
