"""Unit tests for clawdbot.vlm_calibration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from clawdbot.vlm_calibration import (
    CalibrationResult,
    _mean,
    _per_dimension_accuracy,
    calibrate_vlm_scorer,
)


@dataclass
class FakeScore:
    overall: float = 0.0
    hierarchy: float = 0.0
    spacing: float = 0.0
    typography: float = 0.0
    color_usage: float = 0.0
    component_quality: float = 0.0
    issues: list[str] = field(default_factory=list)
    actionable_fixes: list[str] = field(default_factory=list)
    passed: bool = False


def _write_manifest(path: Path, examples: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, "examples": examples}))


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 1x1 transparent PNG
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x00\x03\x00\x01\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _make_scorer(good_score: float, bad_score: float):
    async def scorer(png_bytes: bytes, section_type: str) -> FakeScore:
        # Distinguish good/bad via file path convention: caller writes
        # tests/_tmp/good/<file> vs _tmp/bad/<file>. Since scorer gets
        # bytes only, we route via section_type presence in
        # closure-captured maps. Simpler: return good if bytes length
        # is odd (we write distinct bytes in the test).
        if len(png_bytes) % 2 == 1:
            s = good_score
        else:
            s = bad_score
        return FakeScore(
            overall=s,
            hierarchy=s,
            spacing=s,
            typography=s,
            color_usage=s,
            component_quality=s,
        )

    return scorer


@pytest.mark.asyncio
async def test_calibrate_vlm_scorer_pass(tmp_path: Path) -> None:
    """Good mean >= 7.5, bad mean <= 4.0, gap >= 3.0 => passed."""
    calib_dir = tmp_path / "qa-calibration"
    good_dir = calib_dir / "good"
    bad_dir = calib_dir / "bad"

    _write_manifest(
        good_dir / "manifest.yaml",
        [
            {"file": "g1.png", "section_type": "hero", "expected_score": 9.0},
            {"file": "g2.png", "section_type": "features", "expected_score": 8.5},
        ],
    )
    _write_manifest(
        bad_dir / "manifest.yaml",
        [
            {"file": "b1.png", "section_type": "hero", "expected_score": 3.0},
            {"file": "b2.png", "section_type": "features", "expected_score": 2.5},
        ],
    )

    # Good files: odd-length bytes (triggers good_score path in scorer)
    (good_dir / "g1.png").write_bytes(b"GOOD")  # len=4 even -> bad path
    # Force lengths to distinguish good/bad: use explicit scorer instead.
    async def scorer(png: bytes, stype: str) -> FakeScore:
        return FakeScore(
            overall=9.0,
            hierarchy=9.0,
            spacing=9.0,
            typography=9.0,
            color_usage=9.0,
            component_quality=9.0,
        )

    # Override: create all files and use a dispatching scorer that
    # reads the parent dir name via closure on file content.
    (good_dir / "g1.png").write_bytes(b"good-1")
    (good_dir / "g2.png").write_bytes(b"good-2")
    (bad_dir / "b1.png").write_bytes(b"bad-1")
    (bad_dir / "b2.png").write_bytes(b"bad-2")

    async def routed_scorer(png: bytes, stype: str) -> FakeScore:
        if png.startswith(b"good"):
            return FakeScore(
                overall=9.0,
                hierarchy=9.0,
                spacing=9.0,
                typography=9.0,
                color_usage=9.0,
                component_quality=9.0,
            )
        return FakeScore(
            overall=3.0,
            hierarchy=3.0,
            spacing=3.0,
            typography=3.0,
            color_usage=3.0,
            component_quality=3.0,
        )

    result = await calibrate_vlm_scorer(
        calibration_dir=calib_dir,
        scorer=routed_scorer,
        write_last_run=True,
    )

    assert isinstance(result, CalibrationResult)
    assert result.good_count == 2
    assert result.bad_count == 2
    assert result.good_mean_score == pytest.approx(9.0)
    assert result.bad_mean_score == pytest.approx(3.0)
    assert result.separation_gap == pytest.approx(6.0)
    assert result.passed is True
    assert (calib_dir / "last_run.yaml").exists()


@pytest.mark.asyncio
async def test_calibrate_vlm_scorer_fails_low_gap(tmp_path: Path) -> None:
    """Separation gap < 3.0 => passed is False."""
    calib_dir = tmp_path / "qa"
    good_dir = calib_dir / "good"
    bad_dir = calib_dir / "bad"

    _write_manifest(
        good_dir / "manifest.yaml",
        [{"file": "g1.png", "section_type": "hero", "expected_score": 7.0}],
    )
    _write_manifest(
        bad_dir / "manifest.yaml",
        [{"file": "b1.png", "section_type": "hero", "expected_score": 6.0}],
    )
    (good_dir / "g1.png").write_bytes(b"good")
    (bad_dir / "b1.png").write_bytes(b"bad")

    async def narrow_scorer(png: bytes, stype: str) -> FakeScore:
        if png == b"good":
            return FakeScore(overall=7.0)
        return FakeScore(overall=6.0)

    result = await calibrate_vlm_scorer(
        calibration_dir=calib_dir,
        scorer=narrow_scorer,
        write_last_run=False,
    )

    assert result.separation_gap == pytest.approx(1.0)
    assert result.passed is False
    assert any("separation gap" in n for n in result.notes)


def test_separation_gap_calculation() -> None:
    """_mean helper returns correct averages."""
    assert _mean([9.0, 8.0, 7.0]) == pytest.approx(8.0)
    assert _mean([]) == 0.0


@pytest.mark.asyncio
async def test_recommended_threshold_midpoint(tmp_path: Path) -> None:
    """Recommended threshold is the midpoint of good/bad means."""
    calib_dir = tmp_path / "qa"
    good_dir = calib_dir / "good"
    bad_dir = calib_dir / "bad"

    _write_manifest(
        good_dir / "manifest.yaml",
        [{"file": "g1.png", "section_type": "hero", "expected_score": 8.0}],
    )
    _write_manifest(
        bad_dir / "manifest.yaml",
        [{"file": "b1.png", "section_type": "hero", "expected_score": 4.0}],
    )
    (good_dir / "g1.png").write_bytes(b"good")
    (bad_dir / "b1.png").write_bytes(b"bad")

    async def scorer(png: bytes, stype: str) -> FakeScore:
        return FakeScore(overall=8.0 if png == b"good" else 4.0)

    result = await calibrate_vlm_scorer(
        calibration_dir=calib_dir,
        scorer=scorer,
        write_last_run=False,
    )

    assert result.recommended_threshold == pytest.approx(6.0)


def test_per_dimension_accuracy_perfect() -> None:
    """All good dimensions > all bad => accuracy 1.0 per dimension."""
    from clawdbot.vlm_calibration import CalibrationExample

    good = [
        CalibrationExample(
            file="g1.png",
            section_type="hero",
            expected_score=9.0,
            actual_score=9.0,
            actual_dimensions={
                "hierarchy": 9.0,
                "spacing": 9.0,
                "typography": 9.0,
                "color_usage": 9.0,
                "component_quality": 9.0,
            },
        )
    ]
    bad = [
        CalibrationExample(
            file="b1.png",
            section_type="hero",
            expected_score=3.0,
            actual_score=3.0,
            actual_dimensions={
                "hierarchy": 3.0,
                "spacing": 3.0,
                "typography": 3.0,
                "color_usage": 3.0,
                "component_quality": 3.0,
            },
        )
    ]
    acc = _per_dimension_accuracy(good, bad)
    assert acc["hierarchy"] == 1.0
    assert acc["spacing"] == 1.0
    assert acc["typography"] == 1.0
