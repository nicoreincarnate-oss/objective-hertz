"""
DebateQD — Quality-Diversity evolution of persuasion strategies.

Paper: DebateQD (Paper 66, 13.94% better generalization than truth-optimized).

Instead of a static soul_copy.md, maintain a population of N persuasion
strategy variants. Evolve them via MAP-Elites: crossover best performers,
mutate offspring, challenge via Beta, drop worst, keep diversity.

Fitness tracked via bandit framework (reply rate per variant).

Gated behind DEBATE_QD_ENABLED=1 (default 1).
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger("perseus.debate_qd")

DEBATE_QD_ENABLED = os.environ.get("DEBATE_QD_ENABLED", "1") == "1"
MIN_POPULATION = 3
MAX_POPULATION = 7
DEFAULT_POPULATION = 5


class StrategyPool:
    """Manages a population of soul_copy persuasion strategy variants."""

    def __init__(self, variants_dir: Path | None = None):
        self._variants_dir = variants_dir
        self._variants: dict[str, dict] = {}  # name -> {"content": str, "fitness": float}

    @property
    def variants_dir(self) -> Path:
        if self._variants_dir:
            return self._variants_dir
        try:
            from shared.config import config
            return config.root_dir / "soul" / "variants"
        except Exception:
            return Path("/tmp/soul_variants")

    def load_variants(self) -> int:
        """Load variant files from disk. Creates defaults if empty."""
        vdir = self.variants_dir
        vdir.mkdir(parents=True, exist_ok=True)

        count = 0
        for f in vdir.iterdir():
            if f.suffix == ".md" and f.stem.startswith("soul_copy_v"):
                content = f.read_text()
                name = f.stem
                fitness = self._get_fitness(name)
                self._variants[name] = {"content": content, "fitness": fitness, "path": str(f)}
                count += 1

        if count == 0:
            self._create_seed_variants()
            count = len(self._variants)

        return count

    def _create_seed_variants(self) -> None:
        """Create initial variant population from existing soul_copy.md."""
        try:
            from shared.config import config
            base_path = config.root_dir / "soul" / "soul_copy.md"
            if base_path.exists():
                base_content = base_path.read_text()
            else:
                base_content = "Professional, warm, data-driven cold outreach."
        except Exception:
            base_content = "Professional, warm, data-driven cold outreach."

        vdir = self.variants_dir
        vdir.mkdir(parents=True, exist_ok=True)

        # Create 3 seed variants with different tones
        seeds = {
            "soul_copy_v1": base_content,
            "soul_copy_v2": base_content.replace("Professional", "Direct and conversational"),
            "soul_copy_v3": base_content.replace("Professional", "Data-first with specific numbers"),
        }

        for name, content in seeds.items():
            path = vdir / f"{name}.md"
            path.write_text(content)
            self._variants[name] = {"content": content, "fitness": 0.5, "path": str(path)}

    def _get_fitness(self, variant_name: str) -> float:
        """Get fitness score from bandit framework."""
        try:
            from shared.bandit import get_bandit
            stats = get_bandit().get_stats("email_template_variant")
            arm_stats = stats.get("arms", {}).get(variant_name, {})
            return arm_stats.get("mean", 0.5)
        except Exception:
            return 0.5

    def select_variant(self) -> tuple[str, str]:
        """Select a variant for use (via bandit or random if bandit unavailable).

        Returns: (variant_name, content)
        """
        if not self._variants:
            self.load_variants()

        if not self._variants:
            return ("default", "Professional cold outreach.")

        try:
            from shared.bandit import get_bandit, BANDIT_ENABLED
            if BANDIT_ENABLED:
                import asyncio
                arm = asyncio.get_event_loop().run_until_complete(
                    get_bandit().select("email_template_variant", list(self._variants.keys()))
                )
                if arm in self._variants:
                    return (arm, self._variants[arm]["content"])
        except Exception:
            pass

        # Fallback: fitness-weighted random
        variants = list(self._variants.items())
        weights = [max(0.1, v["fitness"]) for _, v in variants]
        chosen_name, chosen = random.choices(variants, weights=weights, k=1)[0]
        return (chosen_name, chosen["content"])

    def get_top_n(self, n: int = 2) -> list[tuple[str, dict]]:
        """Get top N variants by fitness."""
        sorted_v = sorted(self._variants.items(), key=lambda x: x[1]["fitness"], reverse=True)
        return sorted_v[:n]

    def get_worst(self) -> tuple[str, dict] | None:
        """Get the worst-performing variant."""
        if len(self._variants) <= MIN_POPULATION:
            return None
        sorted_v = sorted(self._variants.items(), key=lambda x: x[1]["fitness"])
        return sorted_v[0]

    async def evolve_step(self) -> dict:
        """One evolution step: crossover → mutate → challenge → add/drop.

        Called during sleep cycle when DEBATE_QD_ENABLED=1.
        Returns: {"action": str, "new_variant": str, "dropped": str}
        """
        if not DEBATE_QD_ENABLED:
            return {"action": "disabled"}

        if not self._variants:
            self.load_variants()

        if len(self._variants) < 2:
            return {"action": "insufficient_variants"}

        # Select 2 fittest parents
        parents = self.get_top_n(2)
        if len(parents) < 2:
            return {"action": "insufficient_parents"}

        parent_a_name, parent_a = parents[0]
        parent_b_name, parent_b = parents[1]

        # Crossover: LLM merges best elements
        try:
            from shared.llm_client import llm
            offspring_content = await llm.generate(
                f"Merge the best elements of these two cold email strategies:\n\n"
                f"STRATEGY A ({parent_a_name}, fitness={parent_a['fitness']:.2f}):\n"
                f"{parent_a['content'][:500]}\n\n"
                f"STRATEGY B ({parent_b_name}, fitness={parent_b['fitness']:.2f}):\n"
                f"{parent_b['content'][:500]}\n\n"
                f"Create a new strategy that combines the strengths of both. "
                f"Keep it concise (same length as originals). "
                f"Introduce ONE novel element not in either parent.",
                model="smart",
                temperature=0.6,
            )
        except Exception as e:
            return {"action": "crossover_failed", "error": str(e)}

        # Name and save offspring
        gen = len(self._variants) + 1
        offspring_name = f"soul_copy_v{gen}"
        offspring_path = self.variants_dir / f"{offspring_name}.md"
        offspring_path.write_text(offspring_content)
        self._variants[offspring_name] = {
            "content": offspring_content,
            "fitness": 0.5,  # neutral until tested
            "path": str(offspring_path),
        }

        # Drop worst if over population cap
        dropped = ""
        if len(self._variants) > MAX_POPULATION:
            worst = self.get_worst()
            if worst:
                worst_name, worst_data = worst
                worst_path = Path(worst_data.get("path", ""))
                if worst_path.exists():
                    worst_path.unlink()
                del self._variants[worst_name]
                dropped = worst_name

        logger.info(f"DebateQD evolved: parents=({parent_a_name},{parent_b_name}) → {offspring_name}, dropped={dropped or 'none'}")

        return {
            "action": "evolved",
            "new_variant": offspring_name,
            "parents": [parent_a_name, parent_b_name],
            "dropped": dropped,
            "population_size": len(self._variants),
        }


# Singleton
_pool: StrategyPool | None = None


def get_strategy_pool() -> StrategyPool:
    global _pool
    if _pool is None:
        _pool = StrategyPool()
    return _pool
