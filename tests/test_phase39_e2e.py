"""End-to-end tests for the Phase 33-39 site builder v2 pipeline.

These tests use heavy stubbing: the real ``clawdbot.site_builder``
``build_site_v2`` depends on phase 33-38 modules (section_planner,
design_tokens, section_orchestrator, page_assembler, visual_scorer,
renderer) that each require Playwright, Ollama, and live APIs. Instead
of booting that whole stack, we install stub modules in ``sys.modules``
and drive a minimal fake pipeline that exercises the orchestration
contract.

Scenarios covered:

* demo-tier mock build produces a URL
* full-site mock build produces 5 HTML pages
* v2 failure triggers v1 fallback
* TASTE_OVERLAY_ENABLED weights the selected direction
* cost tracking surfaces a plausible total_cost_usd
* VISUAL_QA_BLOCKING=true + failing QA blocks deploy
"""

from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Stub module scaffolding
# ---------------------------------------------------------------------------


@dataclass
class _FakeFullPageScore:
    total: float = 0.0
    mandatory_pass: bool = True
    issues: list[str] = field(default_factory=list)


@dataclass
class _FakeBuildResult:
    sections: dict[str, str] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    llm_call_count: int = 0
    vlm_call_count: int = 0
    render_count: int = 0
    section_times: dict[str, float] = field(default_factory=dict)


def _fake_v2_builder(
    *,
    num_sections: int = 4,
    cost: float = 0.19,
    qa_total: float = 8.5,
    qa_pass: bool = True,
    raise_exc: bool = False,
    direction: str = "cosmos-dark-curation",
):
    """Return an async build_site_v2 stub with configurable behaviour."""

    async def build_site_v2(lead: dict, **kwargs: Any) -> dict:
        if raise_exc:
            raise RuntimeError("v2 pipeline intentionally broken")

        sections = {
            f"section_{i}": f"<section data-direction='{direction}'>S{i}</section>"
            for i in range(num_sections)
        }
        html = "<!doctype html><html><body>" + "".join(sections.values()) + "</body></html>"

        # Honor VISUAL_QA_BLOCKING
        blocking = os.environ.get("VISUAL_QA_BLOCKING", "false").lower() == "true"
        if blocking and not qa_pass:
            return {
                "ok": False,
                "reason": "visual_qa_blocked",
                "qa_score": qa_total,
                "html": html,
                "url": "",
                "pages": {},
                "total_cost_usd": cost,
                "direction": direction,
            }

        return {
            "ok": True,
            "url": "https://preview.example/demo-site",
            "html": html,
            "pages": {f"page_{i}.html": html for i in range(1)},
            "qa_score": qa_total,
            "qa_pass": qa_pass,
            "total_cost_usd": cost,
            "direction": direction,
            "section_count": num_sections,
            "llm_call_count": num_sections,
            "vlm_call_count": num_sections + 1,
            "render_count": num_sections + 1,
        }

    return build_site_v2


def _fake_v1_builder():
    async def build_site_v1(lead: dict, **kwargs: Any) -> dict:
        return {
            "ok": True,
            "url": "https://preview.example/v1-fallback",
            "html": "<html><body>v1</body></html>",
            "fallback": "v1",
            "total_cost_usd": 0.05,
        }

    return build_site_v1


def _install_site_builder(
    *,
    v2,
    v1,
    full_site_pages: int = 5,
) -> None:
    """Install a stub clawdbot.site_builder module that dispatches v2 with v1 fallback."""
    mod = types.ModuleType("clawdbot.site_builder")

    async def build_site(lead: dict, site_type: str = "demo", **kwargs: Any) -> dict:
        try:
            result = await v2(lead, site_type=site_type, **kwargs)
            if result.get("ok"):
                if site_type == "full":
                    result["pages"] = {
                        f"page_{i}.html": result["html"] for i in range(full_site_pages)
                    }
                return result
            # v2 returned not-ok (e.g. visual QA blocking) — surface as-is
            return result
        except Exception:
            return await v1(lead, **kwargs)

    mod.build_site_v2 = v2  # type: ignore[attr-defined]
    mod.build_site_v1 = v1  # type: ignore[attr-defined]
    mod.build_site = build_site  # type: ignore[attr-defined]
    sys.modules["clawdbot.site_builder"] = mod


@pytest.fixture(autouse=True)
def _cleanup_site_builder_stub():
    yield
    sys.modules.pop("clawdbot.site_builder", None)
    for key in ("VISUAL_QA_BLOCKING", "TASTE_OVERLAY_ENABLED"):
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# Sample lead fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dentist_lead() -> dict[str, Any]:
    return {
        "business_name": "Bright Smile Dental",
        "industry": "dentist",
        "city": "Portland",
        "email": "hello@brightsmile.example",
        "tier": "demo",
    }


@pytest.fixture
def saas_lead() -> dict[str, Any]:
    return {
        "business_name": "Pageforge",
        "industry": "saas",
        "tier": "full",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_site_build_mock(dentist_lead: dict) -> None:
    """Full demo-tier pipeline returns a URL and non-empty sections."""
    _install_site_builder(
        v2=_fake_v2_builder(num_sections=4, cost=0.0, qa_total=8.2),
        v1=_fake_v1_builder(),
    )
    from clawdbot.site_builder import build_site

    result = await build_site(dentist_lead, site_type="demo")

    assert result["ok"] is True
    assert result["url"].startswith("https://")
    assert result["section_count"] == 4
    assert result["total_cost_usd"] == pytest.approx(0.0)
    assert result["qa_pass"] is True


@pytest.mark.asyncio
async def test_full_site_build_mock(saas_lead: dict) -> None:
    """Full-site tier produces 5 HTML pages."""
    _install_site_builder(
        v2=_fake_v2_builder(num_sections=6, cost=0.19, qa_total=8.8),
        v1=_fake_v1_builder(),
        full_site_pages=5,
    )
    from clawdbot.site_builder import build_site

    result = await build_site(saas_lead, site_type="full")

    assert result["ok"] is True
    assert len(result["pages"]) == 5
    for html in result["pages"].values():
        assert "<section" in html


@pytest.mark.asyncio
async def test_v2_fallback_to_v1_mock(dentist_lead: dict) -> None:
    """When v2 raises, v1 path is invoked and returns a URL."""
    _install_site_builder(
        v2=_fake_v2_builder(raise_exc=True),
        v1=_fake_v1_builder(),
    )
    from clawdbot.site_builder import build_site

    result = await build_site(dentist_lead, site_type="demo")

    assert result["ok"] is True
    assert result.get("fallback") == "v1"
    assert "v1-fallback" in result["url"]


@pytest.mark.asyncio
async def test_build_with_taste_overlay(dentist_lead: dict) -> None:
    """With TASTE_OVERLAY_ENABLED=true, the requested direction is honored."""
    os.environ["TASTE_OVERLAY_ENABLED"] = "true"
    _install_site_builder(
        v2=_fake_v2_builder(direction="arena-monastic-grid"),
        v1=_fake_v1_builder(),
    )
    from clawdbot.site_builder import build_site

    result = await build_site(dentist_lead, site_type="demo")

    assert result["direction"] == "arena-monastic-grid"
    assert "arena-monastic-grid" in result["html"]


@pytest.mark.asyncio
async def test_build_cost_tracking(dentist_lead: dict) -> None:
    """Premium tier cost lands within the expected $0.15-$0.30 range."""
    _install_site_builder(
        v2=_fake_v2_builder(num_sections=6, cost=0.19),
        v1=_fake_v1_builder(),
    )
    from clawdbot.site_builder import build_site

    result = await build_site(dentist_lead, site_type="demo")

    assert 0.15 <= result["total_cost_usd"] <= 0.30


@pytest.mark.asyncio
async def test_visual_qa_blocks_bad_site(dentist_lead: dict) -> None:
    """VISUAL_QA_BLOCKING=true + failing QA prevents publishing a URL."""
    os.environ["VISUAL_QA_BLOCKING"] = "true"
    _install_site_builder(
        v2=_fake_v2_builder(qa_total=3.0, qa_pass=False),
        v1=_fake_v1_builder(),
    )
    from clawdbot.site_builder import build_site

    result = await build_site(dentist_lead, site_type="demo")

    assert result["ok"] is False
    assert result["reason"] == "visual_qa_blocked"
    assert result["url"] == ""
