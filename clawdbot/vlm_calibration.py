"""VLM calibration harness for the ClawdBot v2 visual scorer.

The section scoring pipeline depends on Qwen2.5-VL (or Claude Vision)
returning *meaningfully discriminative* scores. Without calibration we
have no idea whether a reported score of 7.5 actually means "good" or
is just the VLM's default middle-of-the-road output.

This module runs a fixed set of known-good and known-bad section
screenshots through :func:`clawdbot.visual_scorer.score_section_quality`
and reports:

* mean score for the good set (target: >= 7.5)
* mean score for the bad set (target: <= 4.0)
* separation gap = good_mean - bad_mean (target: >= 3.0)
* per-dimension accuracy (which dimensions discriminate best)
* recommended pass/fail threshold based on the midpoint

If the separation gap falls below 3.0 the calibration is considered a
FAIL and ``CalibrationResult.passed`` is False — the caller should
tighten the VLM prompt, switch providers, or add heuristic signals
before trusting section scores.

The calibration dataset lives at ``soul/qa-calibration/``::

    soul/qa-calibration/
    |-- good/
    |   |-- manifest.yaml       # list of good examples + expected scores
    |   |-- good_hero_01.png    # populated by operators / capture tools
    |-- bad/
    |   |-- manifest.yaml
    |   |-- bad_hero_01.png
    `-- last_run.yaml           # most recent calibration result
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a hard dep in prod
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger("perseus.clawdbot.vlm_calibration")

DEFAULT_CALIBRATION_DIR = Path("soul/qa-calibration")
MIN_SEPARATION_GAP = 3.0
TARGET_GOOD_MEAN = 7.5
TARGET_BAD_MEAN = 4.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CalibrationExample:
    """One labelled example in the calibration dataset."""

    file: str
    section_type: str
    expected_score: float
    notes: str = ""
    actual_score: float = 0.0
    actual_dimensions: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass
class CalibrationResult:
    """Output of :func:`calibrate_vlm_scorer`."""

    good_mean_score: float = 0.0
    bad_mean_score: float = 0.0
    separation_gap: float = 0.0
    recommended_threshold: float = 0.0
    passed: bool = False
    per_dimension_accuracy: dict[str, float] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)
    good_count: int = 0
    bad_count: int = 0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_manifest(manifest_path: Path) -> list[CalibrationExample]:
    """Load a manifest.yaml into a list of :class:`CalibrationExample`."""
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    if not manifest_path.exists():
        return []
    raw = yaml.safe_load(manifest_path.read_text()) or {}
    entries = raw.get("examples", [])
    if not isinstance(entries, list):
        return []
    examples: list[CalibrationExample] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        examples.append(
            CalibrationExample(
                file=str(entry.get("file", "")),
                section_type=str(entry.get("section_type", "")),
                expected_score=float(entry.get("expected_score", 0.0)),
                notes=str(entry.get("notes", "")),
            )
        )
    return examples


# ---------------------------------------------------------------------------
# Scoring wrapper
# ---------------------------------------------------------------------------


async def _score_example(
    example: CalibrationExample,
    base_dir: Path,
    scorer: Any | None,
) -> CalibrationExample:
    """Run a single example through the VLM scorer."""
    png_path = base_dir / example.file
    if not png_path.exists():
        example.error = f"file not found: {png_path}"
        return example

    try:
        png_bytes = png_path.read_bytes()
    except OSError as exc:
        example.error = f"read error: {exc}"
        return example

    if scorer is None:
        try:
            from clawdbot.visual_scorer import (  # type: ignore[no-redef]
                score_section_quality as scorer,
            )
        except ImportError as exc:
            example.error = f"visual_scorer unavailable: {exc}"
            return example

    try:
        score = await scorer(png_bytes, example.section_type)
    except Exception as exc:  # noqa: BLE001 - per-example errors must not abort run
        example.error = f"score error: {exc}"
        return example

    example.actual_score = float(getattr(score, "overall", 0.0) or 0.0)
    example.actual_dimensions = {
        "hierarchy": float(getattr(score, "hierarchy", 0.0) or 0.0),
        "spacing": float(getattr(score, "spacing", 0.0) or 0.0),
        "typography": float(getattr(score, "typography", 0.0) or 0.0),
        "color_usage": float(getattr(score, "color_usage", 0.0) or 0.0),
        "component_quality": float(
            getattr(score, "component_quality", 0.0) or 0.0
        ),
    }
    return example


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _per_dimension_accuracy(
    good: list[CalibrationExample],
    bad: list[CalibrationExample],
) -> dict[str, float]:
    """Fraction of pairs where the dimension scores higher on the good example.

    Accuracy > 0.5 means the dimension discriminates in the expected
    direction. A dimension near 0.5 is noise; below 0.5 means the
    dimension is *inverted* and needs prompt fixes.
    """
    dims = ("hierarchy", "spacing", "typography", "color_usage", "component_quality")
    out: dict[str, float] = {}
    if not good or not bad:
        return {d: 0.0 for d in dims}

    for dim in dims:
        wins = 0
        total = 0
        for g in good:
            if g.error:
                continue
            for b in bad:
                if b.error:
                    continue
                total += 1
                g_val = g.actual_dimensions.get(dim, 0.0)
                b_val = b.actual_dimensions.get(dim, 0.0)
                if g_val > b_val:
                    wins += 1
                elif g_val == b_val:
                    wins += 0.5
        out[dim] = wins / total if total else 0.0
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def calibrate_vlm_scorer(
    calibration_dir: Path = DEFAULT_CALIBRATION_DIR,
    *,
    scorer: Any | None = None,
    write_last_run: bool = True,
) -> CalibrationResult:
    """Run all calibration examples through the VLM scorer.

    Args:
        calibration_dir: Directory containing ``good/`` and ``bad/``
            subdirectories, each with a ``manifest.yaml``.
        scorer: Optional async callable ``(png_bytes, section_type) -> SectionScore``.
            Defaults to :func:`clawdbot.visual_scorer.score_section_quality`
            (imported lazily). Tests inject a mock here.
        write_last_run: If True, persist the result to
            ``{calibration_dir}/last_run.yaml``.

    Returns:
        :class:`CalibrationResult` with per-example details and summary
        metrics.
    """
    good_dir = calibration_dir / "good"
    bad_dir = calibration_dir / "bad"

    good_examples = _load_manifest(good_dir / "manifest.yaml")
    bad_examples = _load_manifest(bad_dir / "manifest.yaml")

    for ex in good_examples:
        await _score_example(ex, good_dir, scorer)
    for ex in bad_examples:
        await _score_example(ex, bad_dir, scorer)

    good_scores = [ex.actual_score for ex in good_examples if not ex.error]
    bad_scores = [ex.actual_score for ex in bad_examples if not ex.error]

    good_mean = _mean(good_scores)
    bad_mean = _mean(bad_scores)
    gap = good_mean - bad_mean
    threshold = (good_mean + bad_mean) / 2 if (good_scores and bad_scores) else 6.0
    passed = (
        gap >= MIN_SEPARATION_GAP
        and good_mean >= TARGET_GOOD_MEAN
        and bad_mean <= TARGET_BAD_MEAN
    )

    dim_accuracy = _per_dimension_accuracy(good_examples, bad_examples)

    notes: list[str] = []
    if not good_examples:
        notes.append("no good examples loaded")
    if not bad_examples:
        notes.append("no bad examples loaded")
    if gap < MIN_SEPARATION_GAP:
        notes.append(
            f"separation gap {gap:.2f} below minimum {MIN_SEPARATION_GAP:.2f}"
        )
    if good_mean < TARGET_GOOD_MEAN:
        notes.append(f"good mean {good_mean:.2f} below target {TARGET_GOOD_MEAN}")
    if bad_mean > TARGET_BAD_MEAN:
        notes.append(f"bad mean {bad_mean:.2f} above target {TARGET_BAD_MEAN}")

    result = CalibrationResult(
        good_mean_score=round(good_mean, 3),
        bad_mean_score=round(bad_mean, 3),
        separation_gap=round(gap, 3),
        recommended_threshold=round(threshold, 3),
        passed=passed,
        per_dimension_accuracy={k: round(v, 3) for k, v in dim_accuracy.items()},
        details=[asdict(ex) for ex in good_examples + bad_examples],
        good_count=len(good_examples),
        bad_count=len(bad_examples),
        notes=notes,
    )

    if write_last_run and yaml is not None:
        try:
            calibration_dir.mkdir(parents=True, exist_ok=True)
            (calibration_dir / "last_run.yaml").write_text(
                yaml.safe_dump(asdict(result), default_flow_style=False, sort_keys=False)
            )
        except OSError as exc:
            logger.warning("failed to write last_run.yaml: %s", exc)

    return result
