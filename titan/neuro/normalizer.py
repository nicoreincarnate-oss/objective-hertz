"""Running percentile normalization for neuro-scores.

First BASELINE_SIZE emails establish baseline distribution per dimension.
After baseline: scores normalized to 0-1 using percentile ranks.
Warm-start: initial distribution seeded so pre-baseline scores are not garbage.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
from scipy.stats import percentileofscore

logger = logging.getLogger("neuro.normalizer")


class RunningNormalizer:
    """Running percentile normalization with warm-start.

    Scores are accumulated per dimension.  Once BASELINE_SIZE samples exist
    for a dimension, percentile-based normalization kicks in (0-1 output).

    Before baseline is ready, a sigmoid-like warm-start mapping is used
    to keep scores centered around 0.5.
    """

    BASELINE_SIZE: int = 50

    def __init__(self) -> None:
        self._history: dict[str, list[float]] = defaultdict(list)

    @property
    def baseline_ready(self) -> bool:
        """True if all 4 core dimensions have reached baseline size."""
        dims = ["self_relevance", "trust", "cognitive_ease", "emotional_resonance"]
        return all(
            len(self._history[d]) >= self.BASELINE_SIZE for d in dims
        )

    @property
    def sample_count(self) -> int:
        """Return the minimum sample count across all tracked dimensions."""
        if not self._history:
            return 0
        return min(len(v) for v in self._history.values())

    def normalize(self, raw_scores: dict[str, float]) -> dict[str, float]:
        """Normalize raw scores to 0-1 using running percentile.

        Args:
            raw_scores: dict mapping dimension name -> raw activation value.

        Returns:
            dict mapping dimension name -> normalized value in [0, 1].
        """
        normalized: dict[str, float] = {}

        for dim, value in raw_scores.items():
            self._history[dim].append(value)

            if len(self._history[dim]) >= self.BASELINE_SIZE:
                # Percentile-based normalization
                pct = percentileofscore(self._history[dim], value) / 100.0
                normalized[dim] = float(np.clip(pct, 0.0, 1.0))
            else:
                # Warm-start: sigmoid-like mapping centered at 0.5
                # Maps any real value to (0.2, 0.8) range
                normalized[dim] = 0.5 + (value / (abs(value) + 1e-6)) * 0.3

        return normalized

    def reset(self) -> None:
        """Clear all history (for testing or recalibration)."""
        self._history.clear()

    def get_history(self, dimension: str) -> list[float]:
        """Return recorded history for a dimension (read-only copy)."""
        return list(self._history.get(dimension, []))
