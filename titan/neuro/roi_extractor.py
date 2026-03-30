"""ROI extraction: maps TRIBE v2 vertex predictions to named brain regions.

Uses the Desikan-Killiany atlas on fsaverage5 surface to aggregate vertex-level
activation into 4 cognitive dimension scores.

When nibabel is not installed, uses SYNTHETIC_ATLAS with fake vertex indices
that map to column positions in fallback activation arrays.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("neuro.roi_extractor")

# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------

_NIBABEL_AVAILABLE = False

try:
    import nibabel as nib  # noqa: F401

    _NIBABEL_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Desikan-Killiany atlas region mappings
# ---------------------------------------------------------------------------

DIMENSION_ROIS: dict[str, list[str]] = {
    "self_relevance": [
        "medialorbitofrontal",   # mPFC
        "posteriorcingulate",    # PCC
        "precuneus",             # precuneus
        "inferiorparietal",      # angular gyrus
    ],
    "trust": [
        "superiortemporal",      # STS
        "bankssts",              # posterior STS
        "supramarginal",         # SMG
        "rostralmiddlefrontal",  # rMFG
    ],
    "cognitive_load": [  # NOTE: inverted to get "ease" in extract_scores
        "caudalmiddlefrontal",   # premotor
        "lateralorbitofrontal",  # lOFC
        "parsopercularis",       # Broca's (BA44)
        "parstriangularis",      # Broca's (BA45)
    ],
    "emotional_resonance": [
        "insula",                     # anterior insula
        "medialorbitofrontal",        # vmPFC overlap
        "rostralanteriorcingulate",   # rACC
    ],
}


# Synthetic atlas for fallback mode.  Maps dimension columns (0-3) from the
# fallback activation array to region names.  This lets ROIExtractor.extract_scores
# work identically whether we have real vertex data or synthetic 4-column data.
SYNTHETIC_ATLAS: dict[str, np.ndarray] = {}

_DIM_ORDER = ["self_relevance", "trust", "cognitive_load", "emotional_resonance"]


def _build_synthetic_atlas() -> dict[str, np.ndarray]:
    """Build synthetic atlas mapping region names -> column indices."""
    atlas: dict[str, np.ndarray] = {}
    for dim_idx, dim_name in enumerate(_DIM_ORDER):
        for region in DIMENSION_ROIS[dim_name]:
            # Each region maps to the single column for its dimension
            if region not in atlas:
                atlas[region] = np.array([dim_idx])
            # If region appears in multiple dimensions (e.g., medialorbitofrontal),
            # append the additional column index
            elif dim_idx not in atlas[region]:
                atlas[region] = np.append(atlas[region], dim_idx)
    return atlas


SYNTHETIC_ATLAS = _build_synthetic_atlas()


# ---------------------------------------------------------------------------
# ROIExtractor
# ---------------------------------------------------------------------------

class ROIExtractor:
    """Maps TRIBE v2 vertex predictions to named brain regions.

    With nibabel + fsaverage5 parcellation: real atlas lookup.
    Without nibabel: synthetic atlas that maps to fallback column positions.
    """

    def __init__(self) -> None:
        self._atlas = self._load_atlas()

    def _load_atlas(self) -> dict[str, np.ndarray]:
        """Load fsaverage5 Desikan-Killiany parcellation.

        Returns dict mapping region name -> array of vertex indices.
        Falls back to SYNTHETIC_ATLAS when nibabel is unavailable.
        """
        if not _NIBABEL_AVAILABLE:
            logger.debug("nibabel unavailable -- using synthetic atlas")
            return SYNTHETIC_ATLAS

        try:
            import nibabel as _nib

            # Load fsaverage5 Desikan-Killiany annotation
            # tribev2 provides these files; path may vary by installation
            atlas: dict[str, np.ndarray] = {}
            for hemi in ("lh", "rh"):
                try:
                    annot_path = f"fsaverage5/label/{hemi}.aparc.annot"
                    labels, ctab, names = _nib.freesurfer.read_annot(annot_path)
                    for idx, name_bytes in enumerate(names):
                        name = (
                            name_bytes.decode("utf-8")
                            if isinstance(name_bytes, bytes)
                            else str(name_bytes)
                        )
                        vertices = np.where(labels == idx)[0]
                        if name in atlas:
                            atlas[name] = np.concatenate([atlas[name], vertices])
                        else:
                            atlas[name] = vertices
                except FileNotFoundError:
                    logger.warning("Atlas file %s not found, using synthetic", annot_path)
                    return SYNTHETIC_ATLAS
            return atlas if atlas else SYNTHETIC_ATLAS
        except Exception as exc:
            logger.warning("Atlas loading failed (%s), using synthetic", exc)
            return SYNTHETIC_ATLAS

    def extract_scores(self, activation: np.ndarray) -> dict[str, float]:
        """Extract 4 cognitive dimension scores from vertex activation map.

        Args:
            activation: ndarray, either (n_timesteps, n_vertices) from TRIBE v2
                        or (1, 4) from fallback scorer.

        Returns:
            Dict with keys: self_relevance, trust, cognitive_ease, emotional_resonance.
            Values are raw (unnormalized) aggregated activation scores.
        """
        scores: dict[str, float] = {}

        for dimension, regions in DIMENSION_ROIS.items():
            region_activations: list[float] = []
            for region in regions:
                vertex_indices = self._atlas.get(region, np.array([]))
                if len(vertex_indices) > 0:
                    # Filter indices that are within the activation array bounds
                    valid = vertex_indices[vertex_indices < activation.shape[-1]]
                    if len(valid) > 0:
                        region_mean = float(np.mean(activation[:, valid]))
                        region_activations.append(region_mean)

            if region_activations:
                scores[dimension] = float(np.mean(region_activations))

        # Invert cognitive_load -> cognitive_ease
        if "cognitive_load" in scores:
            scores["cognitive_ease"] = -scores.pop("cognitive_load")

        return scores

    @property
    def is_native(self) -> bool:
        """True if using real fsaverage5 atlas, False if synthetic."""
        return _NIBABEL_AVAILABLE and self._atlas is not SYNTHETIC_ATLAS
