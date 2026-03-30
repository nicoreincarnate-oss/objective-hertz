#!/usr/bin/env python3
"""TRIBE v2 MPS validation spike for Mac M4 32GB.

This script validates whether Meta's TRIBE v2 brain-encoding model can run
on Apple Silicon with acceptable latency and memory footprint.

STATUS: TRIBE v2 / torch / nibabel are NOT installed.
This script documents the intended validation procedure and benchmarks.
When the dependencies become available, uncomment the real implementation.

Decision gate thresholds:
  - PASS:    latency < 8s on MPS, memory < 10GB  -> direct TRIBE v2
  - PARTIAL: latency 8-15s                        -> batch scoring
  - FAIL:    won't load on 32GB                   -> Haiku proxy scorer

Current decision: FAIL (dependencies not installed)
Fallback active: Haiku-based text-analysis proxy scorer in titan/neuro/tribe_service.py
"""

from __future__ import annotations

import gc
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("tribe-spike")

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

_TORCH_AVAILABLE = False
_TRIBE_AVAILABLE = False

try:
    import torch  # noqa: F401

    _TORCH_AVAILABLE = True
except ImportError:
    logger.warning("torch not installed -- spike will run in dry-run mode")

try:
    from tribev2 import TribeModel  # noqa: F401

    _TRIBE_AVAILABLE = True
except ImportError:
    logger.warning("tribev2 not installed -- spike will run in dry-run mode")


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def _memory_mb() -> float:
    """Return current process RSS in MB (macOS/Linux)."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_maxrss / (1024 * 1024)  # macOS reports bytes
    except Exception:
        return 0.0


SAMPLE_EMAIL = (
    "Hi Dr. Martinez, I noticed your dental practice on West Elm Street "
    "doesn't have online booking yet. Most patients under 40 expect to "
    "book appointments from their phone. I built a simple booking page "
    "for a practice like yours -- would you like to see a demo?"
)


def run_spike() -> dict:
    """Run the validation spike, returning benchmark results."""
    results: dict = {
        "torch_available": _TORCH_AVAILABLE,
        "tribe_available": _TRIBE_AVAILABLE,
        "decision": "FAIL",
        "reason": "",
    }

    if not _TORCH_AVAILABLE or not _TRIBE_AVAILABLE:
        results["reason"] = (
            "Dependencies not installed. "
            "Using Haiku-based proxy scorer as fallback."
        )
        logger.info("SPIKE RESULT: %s", results)
        return results

    # -- Real spike (uncomment when deps available) --
    # import torch
    # from tribev2 import TribeModel
    #
    # device = "mps" if torch.backends.mps.is_available() else "cpu"
    # logger.info("Device: %s", device)
    #
    # # 1. Load model
    # mem_before = _memory_mb()
    # t0 = time.monotonic()
    # model = TribeModel.from_pretrained("facebook/tribev2", cache_folder="./cache")
    # model = model.to(device)
    # load_time = time.monotonic() - t0
    # mem_loaded = _memory_mb()
    # results["load_time_s"] = round(load_time, 2)
    # results["memory_loaded_mb"] = round(mem_loaded - mem_before, 1)
    # logger.info("Load: %.1fs, Memory: %.0f MB", load_time, mem_loaded - mem_before)
    #
    # # 2. Inference
    # events_df = model.get_events_dataframe(text=SAMPLE_EMAIL)
    # t0 = time.monotonic()
    # with torch.no_grad():
    #     activation = model.predict(events=events_df)
    # inference_time = time.monotonic() - t0
    # results["inference_time_s"] = round(inference_time, 2)
    # results["activation_shape"] = list(activation.shape)
    # logger.info("Inference: %.1fs, Shape: %s", inference_time, activation.shape)
    #
    # # 3. Unload
    # del model
    # gc.collect()
    # if torch.backends.mps.is_available():
    #     torch.mps.empty_cache()
    # mem_after = _memory_mb()
    # results["memory_after_unload_mb"] = round(mem_after, 1)
    # logger.info("After unload: %.0f MB", mem_after)
    #
    # # 4. Decision
    # if results["memory_loaded_mb"] > 16000:
    #     results["decision"] = "FAIL"
    #     results["reason"] = "Memory exceeds 16GB -- won't fit alongside daemons"
    # elif inference_time > 15:
    #     results["decision"] = "FAIL"
    #     results["reason"] = "Inference > 15s -- too slow even for batch"
    # elif inference_time > 8:
    #     results["decision"] = "PARTIAL"
    #     results["reason"] = "Inference 8-15s -- use batch scoring"
    # else:
    #     results["decision"] = "PASS"
    #     results["reason"] = "Inference < 8s, memory acceptable"

    logger.info("SPIKE RESULT: %s", results)
    return results


if __name__ == "__main__":
    result = run_spike()
    sys.exit(0 if result["decision"] in ("PASS", "PARTIAL") else 1)
