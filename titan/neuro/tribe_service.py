"""TRIBE v2 inference service with lazy load/unload for M4 32GB memory management.

When torch/tribev2 are not installed, falls back to a Haiku-based text-analysis
proxy that returns synthetic activation vectors from LLM scoring rubric responses.

Singleton pattern: one model instance shared across all scoring calls.
"""

from __future__ import annotations

import gc
import json
import logging
import os
from typing import TYPE_CHECKING

import numpy as np

logger = logging.getLogger("neuro.tribe_service")

# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------

_TORCH_AVAILABLE = False
_TRIBE_AVAILABLE = False

try:
    import torch  # noqa: F401

    _TORCH_AVAILABLE = True
except ImportError:
    pass

try:
    from tribev2 import TribeModel  # noqa: F401

    _TRIBE_AVAILABLE = True
except ImportError:
    pass

if TYPE_CHECKING:
    pass


def is_tribe_available() -> bool:
    """Return True if native TRIBE v2 inference is possible."""
    return _TORCH_AVAILABLE and _TRIBE_AVAILABLE


# ---------------------------------------------------------------------------
# Haiku fallback scorer
# ---------------------------------------------------------------------------

# Dimension keywords for text-heuristic fallback (no LLM needed)
_SELF_RELEVANCE_KEYWORDS = [
    "you", "your", "yourself", "name", "business", "practice",
    "company", "shop", "clinic", "office", "store",
]
_TRUST_KEYWORDS = [
    "specific", "data", "research", "found", "noticed", "saw",
    "reviewed", "checked", "looked", "fact", "evidence", "peer",
]
_COGNITIVE_LOAD_KEYWORDS = [
    "however", "furthermore", "additionally", "notwithstanding",
    "consequently", "henceforth", "whereby", "wherein", "thereof",
]
_EMOTIONAL_KEYWORDS = [
    "grow", "freedom", "reputation", "losing", "waste", "save",
    "dream", "fear", "hope", "love", "worry", "excited", "pain",
    "frustrated", "opportunity", "success", "struggle", "thrive",
]


def _keyword_score(text: str, keywords: list[str]) -> float:
    """Count keyword hits normalized to 0-1 range."""
    text_lower = text.lower()
    words = text_lower.split()
    if not words:
        return 0.0
    hits = sum(1 for w in words if any(kw in w for kw in keywords))
    # Normalize: 1 hit per 10 words = 0.5, cap at 1.0
    density = hits / max(len(words), 1) * 10.0
    return min(density, 1.0)


async def _haiku_fallback_scores(text: str) -> np.ndarray:
    """Generate synthetic activation scores via text heuristics.

    Returns ndarray of shape (1, 4) representing mean activation per dimension:
    [self_relevance, trust, cognitive_load (raw, NOT inverted), emotional_resonance]

    Attempts LLM-based scoring first (Haiku via shared.llm_client). If LLM is
    unavailable, falls back to pure keyword heuristics.
    """
    # Try LLM-based scoring
    try:
        from shared.llm_client import LLMClient

        llm = LLMClient()
        prompt = (
            "Score this text on 4 cognitive dimensions (0.0 to 1.0 each). "
            "Return ONLY valid JSON with keys: self_relevance, trust, "
            "cognitive_load, emotional_resonance.\n\n"
            f"Text: {text[:500]}"
        )
        response = await llm.generate(
            prompt,
            system="You are a neuroscience text scorer. Return only JSON.",
            model="auto",
            max_tokens=100,
            temperature=0.1,
            operation="titan.score_with_llm",
            daemon_name="titan",
        )
        data = json.loads(response.strip())
        scores = np.array([[
            float(data.get("self_relevance", 0.5)),
            float(data.get("trust", 0.5)),
            float(data.get("cognitive_load", 0.5)),
            float(data.get("emotional_resonance", 0.5)),
        ]])
        return scores
    except Exception:
        logger.debug("LLM fallback unavailable, using keyword heuristics")

    # Pure keyword heuristic fallback
    sr = _keyword_score(text, _SELF_RELEVANCE_KEYWORDS)
    tr = _keyword_score(text, _TRUST_KEYWORDS)
    cl = _keyword_score(text, _COGNITIVE_LOAD_KEYWORDS)
    em = _keyword_score(text, _EMOTIONAL_KEYWORDS)

    return np.array([[sr, tr, cl, em]])


# ---------------------------------------------------------------------------
# TribeService
# ---------------------------------------------------------------------------

class TribeService:
    """TRIBE v2 inference service with lazy load/unload for M4 32GB memory.

    Singleton access via ``TribeService.get()``.  When TRIBE v2 / torch are
    not installed the service automatically falls back to Haiku-based text
    analysis that returns synthetic activation arrays compatible with
    ``ROIExtractor.extract_scores``.
    """

    _instance: TribeService | None = None
    _model: object | None = None
    _loaded: bool = False
    _device: str = "cpu"

    @classmethod
    def get(cls) -> TribeService:
        """Singleton access."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None
        cls._model = None
        cls._loaded = False

    @property
    def is_native(self) -> bool:
        """True if using real TRIBE v2 model, False if using fallback."""
        return is_tribe_available() and self._loaded

    @property
    def is_loaded(self) -> bool:
        """True if model (or fallback) is ready for inference."""
        return self._loaded or not is_tribe_available()

    async def predict_activation(self, text: str) -> np.ndarray:
        """Text -> predicted cortical activation map.

        With TRIBE v2: returns ndarray shape (n_timesteps, n_vertices) on
        fsaverage5 surface.

        With fallback: returns ndarray shape (1, 4) with synthetic dimension
        scores [self_relevance, trust, cognitive_load, emotional_resonance].
        """
        if not is_tribe_available():
            return await _haiku_fallback_scores(text)

        if not self._loaded:
            await self._load_model()

        import torch as _torch

        events_df = self._text_to_events(text)
        with _torch.no_grad():
            activation = self._model.predict(events=events_df)  # type: ignore[union-attr]
        return np.asarray(activation)

    async def _load_model(self) -> None:
        """Load TRIBE v2 model weights. ~8-10GB on M4."""
        if not is_tribe_available():
            logger.info("TRIBE v2 not available -- using fallback scorer")
            return

        import torch as _torch
        from tribev2 import TribeModel as _TribeModel

        self._device = "mps" if _torch.backends.mps.is_available() else "cpu"
        cache = os.environ.get("TRIBE_CACHE_DIR", "./cache")
        self._model = _TribeModel.from_pretrained(
            "facebook/tribev2", cache_folder=cache,
        ).to(self._device)
        self._loaded = True
        logger.info("TRIBE v2 loaded on %s", self._device)

    async def unload(self) -> None:
        """Free model memory when not needed."""
        if self._model is not None:
            del self._model
            self._model = None
            self._loaded = False
            gc.collect()
            if _TORCH_AVAILABLE:
                import torch as _torch

                if _torch.backends.mps.is_available():
                    _torch.mps.empty_cache()
            logger.info("TRIBE v2 unloaded, memory freed")

    def _text_to_events(self, text: str) -> object:
        """Convert email text to events DataFrame for TRIBE v2 input."""
        if self._model is None:
            raise RuntimeError("Model not loaded")
        return self._model.get_events_dataframe(text=text)  # type: ignore[union-attr]
