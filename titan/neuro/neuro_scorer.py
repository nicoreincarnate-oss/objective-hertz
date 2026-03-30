"""NeuroScorer orchestrator: text -> TribeService -> ROIExtractor -> Normalizer -> NeuroScores.

Produces 4-dimension cognitive scores with configurable composite weighting.
Feature flag: ENABLE_NEURO_SCORER (env var or system_config DB).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field

from titan.neuro.normalizer import RunningNormalizer
from titan.neuro.roi_extractor import ROIExtractor
from titan.neuro.tribe_service import TribeService

logger = logging.getLogger("neuro.scorer")


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Check whether the neuro-scorer is active."""
    return os.environ.get("ENABLE_NEURO_SCORER", "").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# NeuroScores dataclass
# ---------------------------------------------------------------------------

@dataclass
class NeuroScores:
    """4-dimension cognitive score output from the neuro-scorer."""

    self_relevance: float = 0.5       # 0.0-1.0
    trust: float = 0.5                # 0.0-1.0
    cognitive_ease: float = 0.5       # 0.0-1.0
    emotional_resonance: float = 0.5  # 0.0-1.0
    composite: float = 0.5            # weighted combination of above
    raw_roi_activations: dict = field(default_factory=dict)
    dimension_weights: dict = field(default_factory=dict)
    inference_mode: str = "fallback"  # "native" or "fallback"
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        return asdict(self)


# ---------------------------------------------------------------------------
# NeuroScorer
# ---------------------------------------------------------------------------

class NeuroScorer:
    """Orchestrates TRIBE v2 prediction -> ROI extraction -> normalization -> scoring.

    Maintains shared normalizer instance across calls for running percentile tracking.
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "self_relevance": 0.30,
        "trust": 0.25,
        "cognitive_ease": 0.20,
        "emotional_resonance": 0.25,
    }

    _normalizer: RunningNormalizer | None = None

    def __init__(self) -> None:
        # Share normalizer across scorer instances for running percentile
        if NeuroScorer._normalizer is None:
            NeuroScorer._normalizer = RunningNormalizer()
        self._extractor = ROIExtractor()

    async def score(self, text: str) -> NeuroScores:
        """Score text on 4 cognitive dimensions.

        Pipeline: text -> TribeService.predict_activation -> ROIExtractor.extract_scores
                  -> RunningNormalizer.normalize -> NeuroScores

        Args:
            text: Content to score (email body, site copy, etc.).

        Returns:
            NeuroScores dataclass with all dimension scores, composite, and metadata.
        """
        t0 = time.monotonic()

        service = TribeService.get()
        activation = await service.predict_activation(text)

        raw_scores = self._extractor.extract_scores(activation)
        normalized = self._normalizer.normalize(raw_scores)  # type: ignore[union-attr]

        weights = await self._get_current_weights()
        composite = sum(
            normalized.get(d, 0.5) * weights.get(d, 0.25)
            for d in self.DEFAULT_WEIGHTS
        )

        latency = (time.monotonic() - t0) * 1000

        return NeuroScores(
            self_relevance=normalized.get("self_relevance", 0.5),
            trust=normalized.get("trust", 0.5),
            cognitive_ease=normalized.get("cognitive_ease", 0.5),
            emotional_resonance=normalized.get("emotional_resonance", 0.5),
            composite=composite,
            raw_roi_activations=raw_scores,
            dimension_weights=weights,
            inference_mode="native" if service.is_native else "fallback",
            latency_ms=round(latency, 1),
        )

    async def _get_current_weights(self) -> dict[str, float]:
        """Load learned weights from system_config, or use defaults.

        Learning loop (learning_loop.py) updates weights in DB when
        statistically significant correlations with conversion are found.
        """
        try:
            from shared.db import get_config

            stored = await get_config("neuro_composite_weights", None)
            if stored and isinstance(stored, dict):
                # Validate all keys present
                if all(k in stored for k in self.DEFAULT_WEIGHTS):
                    return {k: float(v) for k, v in stored.items()}
        except Exception:
            logger.debug("Could not load learned weights, using defaults")

        return dict(self.DEFAULT_WEIGHTS)

    @classmethod
    def reset_normalizer(cls) -> None:
        """Reset shared normalizer (for testing)."""
        cls._normalizer = None
