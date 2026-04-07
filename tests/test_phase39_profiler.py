"""Unit tests for clawdbot.performance_profiler."""

from __future__ import annotations

import asyncio

import pytest

from clawdbot.performance_profiler import (
    STAGE_TARGETS_S,
    BuildProfile,
    _timed,
    profile_build,
)


@pytest.mark.asyncio
async def test_profile_build_records_stages() -> None:
    """build_callable populates stage_times and BuildProfile aggregates."""

    async def fake_build(lead: dict, *, profile: BuildProfile) -> dict:
        async with _timed(profile, "plan"):
            await asyncio.sleep(0.005)
        async with _timed(profile, "contract"):
            await asyncio.sleep(0.002)
        async with _timed(profile, "sections"):
            await asyncio.sleep(0.01)
        async with _timed(profile, "assembly"):
            await asyncio.sleep(0.001)
        async with _timed(profile, "qa"):
            await asyncio.sleep(0.003)
        return {
            "url": "https://example.com/site",
            "section_times": {"hero": 0.004, "features": 0.006},
            "llm_call_count": 4,
            "vlm_call_count": 5,
            "render_count": 4,
        }

    profile = await profile_build(
        lead={"business_name": "Acme"},
        site_type="demo",
        build_callable=fake_build,
    )

    assert profile.success is True
    assert profile.total_time_s > 0
    assert set(profile.stage_times.keys()) == {
        "plan",
        "contract",
        "sections",
        "assembly",
        "qa",
    }
    assert profile.section_times == {"hero": 0.004, "features": 0.006}
    assert profile.llm_call_count == 4
    assert profile.vlm_call_count == 5
    assert profile.render_count == 4
    assert profile.result_url == "https://example.com/site"


@pytest.mark.asyncio
async def test_bottleneck_detection() -> None:
    """Longest stage is reported as bottleneck."""

    async def fake_build(lead: dict, *, profile: BuildProfile) -> dict:
        profile.record_stage("plan", 1.0)
        profile.record_stage("contract", 2.0)
        profile.record_stage("sections", 50.0)  # longest
        profile.record_stage("assembly", 0.5)
        profile.record_stage("qa", 3.0)
        return {"url": "x"}

    profile = await profile_build(
        lead={},
        site_type="demo",
        build_callable=fake_build,
    )

    assert profile.bottleneck == "sections"


@pytest.mark.asyncio
async def test_target_exceedance_flag() -> None:
    """Stages exceeding targets are tracked."""

    async def fake_build(lead: dict, *, profile: BuildProfile) -> dict:
        profile.record_stage("plan", STAGE_TARGETS_S["plan"] + 5.0)
        profile.record_stage("contract", 1.0)
        profile.record_stage("sections", 1.0)
        profile.record_stage("assembly", 1.0)
        profile.record_stage("qa", 1.0)
        return {"url": "x"}

    profile = await profile_build(
        lead={},
        site_type="demo",
        build_callable=fake_build,
    )

    assert any(flag.startswith("plan:") for flag in profile.exceeded_targets)


@pytest.mark.asyncio
async def test_profile_build_captures_error() -> None:
    """Exceptions during build are recorded but BuildProfile is still returned."""

    async def broken_build(lead: dict, *, profile: BuildProfile) -> dict:
        raise RuntimeError("simulated pipeline failure")

    profile = await profile_build(
        lead={},
        site_type="demo",
        build_callable=broken_build,
    )

    assert profile.success is False
    assert profile.error is not None
    assert "simulated" in profile.error


def test_stage_targets_cover_all_stages() -> None:
    """Sanity check that STAGE_TARGETS_S includes every instrumented stage."""
    for stage in ("plan", "contract", "sections", "assembly", "qa"):
        assert stage in STAGE_TARGETS_S
