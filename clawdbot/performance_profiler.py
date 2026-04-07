"""End-to-end performance profiler for the ClawdBot v2 site build pipeline.

Wraps a full :func:`clawdbot.site_builder.build_site_v2` invocation with
timing instrumentation so we can see which stages dominate total build
time and where to focus optimization work.

Measured stages (timings recorded in ``BuildProfile.stage_times``):

* ``plan``            — :func:`section_planner.create_build_plan`
* ``contract``        — :func:`design_tokens.validate_design_contract`
* ``sections``        — :func:`section_orchestrator.build_all_sections`
* ``assembly``        — :func:`page_assembler.assemble_page`
* ``qa``              — :func:`visual_scorer.score_full_page`
* ``deploy``          — remaining time (deploy + upload)

Per-section timings and call counts (LLM + VLM + render) are recorded
separately when the orchestrator returns a ``BuildResult`` with that
metadata.

Usage::

    from clawdbot.performance_profiler import profile_build
    profile = await profile_build(lead, site_type="demo")
    print(profile.bottleneck)  # -> "sections"

Targets (reporting only — does not enforce):

* Build plan: < 15s
* Design contract validation: < 30s
* Per-section: < 45s
* 4-section parallel build (demo): < 3 min
* 6-section parallel build (premium): < 4 min
* Page assembly: < 10s
* Full-page QA: < 30s
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("perseus.clawdbot.performance_profiler")

# ---------------------------------------------------------------------------
# Targets (for reporting only)
# ---------------------------------------------------------------------------

STAGE_TARGETS_S: dict[str, float] = {
    "plan": 15.0,
    "contract": 30.0,
    "sections": 240.0,  # 4 minutes for premium; demo should be well under
    "assembly": 10.0,
    "qa": 30.0,
    "deploy": 60.0,
}

TOTAL_TARGET_DEMO_S = 180.0  # 3 minutes
TOTAL_TARGET_PREMIUM_S = 240.0  # 4 minutes


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BuildProfile:
    """Structured profile of a single build_site_v2 invocation."""

    total_time_s: float = 0.0
    stage_times: dict[str, float] = field(default_factory=dict)
    section_times: dict[str, float] = field(default_factory=dict)
    vlm_call_count: int = 0
    llm_call_count: int = 0
    render_count: int = 0
    bottleneck: str = ""
    site_type: str = "demo"
    success: bool = False
    error: str | None = None
    exceeded_targets: list[str] = field(default_factory=list)
    result_url: str | None = None

    def record_stage(self, name: str, duration_s: float) -> None:
        self.stage_times[name] = round(duration_s, 4)

    def finalize(self) -> None:
        """Compute bottleneck and target-exceedance flags."""
        if self.stage_times:
            self.bottleneck = max(self.stage_times, key=self.stage_times.get)  # type: ignore[arg-type]
        for stage, target in STAGE_TARGETS_S.items():
            dur = self.stage_times.get(stage, 0.0)
            if dur > target:
                self.exceeded_targets.append(f"{stage}:{dur:.1f}s>{target:.0f}s")
        total_target = (
            TOTAL_TARGET_PREMIUM_S if self.site_type == "full" else TOTAL_TARGET_DEMO_S
        )
        if self.total_time_s > total_target:
            self.exceeded_targets.append(
                f"total:{self.total_time_s:.1f}s>{total_target:.0f}s"
            )


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _timed(profile: BuildProfile, stage: str) -> AsyncIterator[None]:
    """Record the wall-clock duration of an async code block."""
    start = time.perf_counter()
    try:
        yield
    finally:
        profile.record_stage(stage, time.perf_counter() - start)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def profile_build(
    lead: dict[str, Any],
    site_type: str = "demo",
    *,
    build_callable: Any | None = None,
) -> BuildProfile:
    """Profile a single end-to-end build.

    Args:
        lead: The lead dict passed to ``build_site_v2``.
        site_type: ``"demo"`` or ``"full"``. Controls total-time target.
        build_callable: Optional injected async callable used for tests.
            Signature: ``async fn(lead, *, profile) -> dict``. Should
            populate ``profile.stage_times`` via its own instrumentation
            (see :func:`_invoke_with_hooks`).

    Returns:
        :class:`BuildProfile` with recorded stage times.
    """
    profile = BuildProfile(site_type=site_type)
    start = time.perf_counter()

    try:
        if build_callable is not None:
            result = await build_callable(lead, profile=profile)
        else:
            result = await _invoke_with_hooks(lead, site_type, profile)
        profile.success = bool(result)
        if isinstance(result, dict):
            profile.result_url = result.get("url") or result.get("site_url")
            profile.llm_call_count = int(result.get("llm_call_count", 0))
            profile.vlm_call_count = int(result.get("vlm_call_count", 0))
            profile.render_count = int(result.get("render_count", 0))
            section_times = result.get("section_times") or {}
            if isinstance(section_times, dict):
                profile.section_times = {
                    str(k): float(v) for k, v in section_times.items()
                }
    except Exception as exc:  # noqa: BLE001 - we want the profile even on failure
        profile.success = False
        profile.error = str(exc)
        logger.exception("profile_build failed")

    profile.total_time_s = round(time.perf_counter() - start, 4)
    profile.finalize()
    return profile


async def _invoke_with_hooks(
    lead: dict[str, Any],
    site_type: str,
    profile: BuildProfile,
) -> dict[str, Any]:
    """Call the real build pipeline with per-stage timing wrappers.

    The real ``build_site_v2`` is monolithic; rather than patching it we
    call each stage manually, wrapping each in a ``_timed`` context. If
    any of the phase 33-38 modules are unavailable (e.g. during unit
    tests) this function raises ImportError and the caller should inject
    ``build_callable``.
    """
    from clawdbot.design_tokens import validate_design_contract
    from clawdbot.page_assembler import assemble_page
    from clawdbot.renderer import PlaywrightPool
    from clawdbot.section_orchestrator import build_all_sections
    from clawdbot.section_planner import create_build_plan
    from clawdbot.visual_scorer import score_full_page

    async with _timed(profile, "plan"):
        plan = await create_build_plan(lead, site_type=site_type)

    async with _timed(profile, "contract"):
        validate_design_contract(plan.design_tokens)

    pool = PlaywrightPool()
    async with _timed(profile, "sections"):
        build_result = await build_all_sections(plan, pool=pool)

    async with _timed(profile, "assembly"):
        page = assemble_page(plan, build_result)

    async with _timed(profile, "qa"):
        qa = await score_full_page(page.html, pool=pool)

    return {
        "url": getattr(build_result, "url", None),
        "section_times": getattr(build_result, "section_times", {}),
        "llm_call_count": getattr(build_result, "llm_call_count", 0),
        "vlm_call_count": getattr(build_result, "vlm_call_count", 0)
        + (1 if qa is not None else 0),
        "render_count": getattr(build_result, "render_count", 0),
    }
