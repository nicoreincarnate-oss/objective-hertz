"""
Bandit Framework — Thompson Sampling with Track-and-Stop convergence.

Papers: MAB for RLHF (Paper 93), Bandits Tutorial AAAI 2026 (Paper 94),
TensorZero Track-and-Stop (Paper 99), A/B Testing Prompts (Paper 100).

Each pipeline decision (email template, subject line, pricing tier) becomes
a multi-armed bandit experiment. Thompson Sampling explores variants
while exploiting winners. Track-and-Stop detects when a winner is found
and locks in — 37% faster than fixed A/B testing.

Usage:
    bandit = get_bandit()
    arm = await bandit.select("email_template_experiment")
    # ... use the arm ...
    await bandit.update("email_template_experiment", arm, reward=1.0)

Gated behind BANDIT_ENABLED=1 (default 1).
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field

logger = logging.getLogger("perseus.bandit")

BANDIT_ENABLED = os.environ.get("BANDIT_ENABLED", "1") == "1"
COLD_START_PULLS = 50  # uniform random for first N pulls per experiment
CONVERGENCE_THRESHOLD = 0.95  # posterior prob best arm > this → converge


@dataclass
class BanditArm:
    """A single arm in a multi-armed bandit experiment."""
    name: str
    alpha: float = 1.0  # Beta distribution: successes + 1
    beta: float = 1.0   # Beta distribution: failures + 1
    total_pulls: int = 0
    total_reward: float = 0.0

    @property
    def mean_reward(self) -> float:
        return self.alpha / (self.alpha + self.beta) if (self.alpha + self.beta) > 0 else 0.5

    def sample(self) -> float:
        """Draw from Beta(alpha, beta) posterior — Thompson Sampling."""
        return random.betavariate(max(0.01, self.alpha), max(0.01, self.beta))


@dataclass
class Experiment:
    """A multi-armed bandit experiment."""
    experiment_id: str
    arms: dict[str, BanditArm] = field(default_factory=dict)
    total_pulls: int = 0
    converged: bool = False
    winner: str = ""

    def add_arm(self, name: str) -> None:
        if name not in self.arms:
            self.arms[name] = BanditArm(name=name)


class BanditPolicy:
    """Thompson Sampling bandit with Track-and-Stop convergence.

    Each experiment has multiple arms. On each pull:
    1. If cold start (< COLD_START_PULLS): uniform random
    2. Else: Thompson Sampling (sample from Beta posterior, pick max)
    3. After each update: check Track-and-Stop convergence
    """

    def __init__(self):
        self._experiments: dict[str, Experiment] = {}

    def _get_or_create(self, experiment_id: str, arms: list[str] | None = None) -> Experiment:
        if experiment_id not in self._experiments:
            exp = Experiment(experiment_id=experiment_id)
            for arm_name in (arms or []):
                exp.add_arm(arm_name)
            self._experiments[experiment_id] = exp
        return self._experiments[experiment_id]

    async def select(self, experiment_id: str, arms: list[str] | None = None) -> str:
        """Select an arm via Thompson Sampling.

        Args:
            experiment_id: unique experiment name
            arms: list of arm names (created on first call)

        Returns: selected arm name
        """
        if not BANDIT_ENABLED:
            return (arms or ["default"])[0]

        exp = self._get_or_create(experiment_id, arms)

        if not exp.arms:
            if arms:
                for a in arms:
                    exp.add_arm(a)
            else:
                return "default"

        # If already converged, return winner
        if exp.converged and exp.winner:
            return exp.winner

        # Cold start: uniform random for exploration
        if exp.total_pulls < COLD_START_PULLS:
            chosen = random.choice(list(exp.arms.keys()))
            logger.debug(f"Bandit {experiment_id}: cold start → {chosen} (pull {exp.total_pulls})")
            return chosen

        # Thompson Sampling: sample from each arm's posterior, pick highest
        best_arm = ""
        best_sample = -1.0
        for name, arm in exp.arms.items():
            sample = arm.sample()
            if sample > best_sample:
                best_sample = sample
                best_arm = name

        logger.debug(f"Bandit {experiment_id}: Thompson → {best_arm} (sample={best_sample:.3f})")
        return best_arm

    async def update(self, experiment_id: str, arm_name: str, reward: float) -> None:
        """Update arm posterior with observed reward.

        reward: 0.0 (failure) to 1.0 (success). Intermediate values supported.
        """
        if not BANDIT_ENABLED:
            return

        exp = self._get_or_create(experiment_id)
        if arm_name not in exp.arms:
            exp.add_arm(arm_name)

        arm = exp.arms[arm_name]
        # Update Beta distribution: success → alpha += reward, failure → beta += (1-reward)
        arm.alpha += reward
        arm.beta += (1.0 - reward)
        arm.total_pulls += 1
        arm.total_reward += reward
        exp.total_pulls += 1

        # Check convergence
        if not exp.converged and exp.total_pulls >= COLD_START_PULLS:
            self._check_convergence(exp)

        # Persist to DB (best-effort)
        await self._persist(exp, arm_name)

    def _check_convergence(self, exp: Experiment) -> None:
        """Track-and-Stop: check if best arm is definitively best.

        Simulates N draws from each arm's posterior.
        If one arm is best in > CONVERGENCE_THRESHOLD fraction of draws,
        the experiment is converged.
        """
        if len(exp.arms) < 2:
            return

        n_simulations = 1000
        win_counts: dict[str, int] = {name: 0 for name in exp.arms}

        for _ in range(n_simulations):
            best_name = ""
            best_val = -1.0
            for name, arm in exp.arms.items():
                val = arm.sample()
                if val > best_val:
                    best_val = val
                    best_name = name
            if best_name:
                win_counts[best_name] += 1

        # Check if any arm wins > threshold of simulations
        for name, wins in win_counts.items():
            prob = wins / n_simulations
            if prob >= CONVERGENCE_THRESHOLD:
                exp.converged = True
                exp.winner = name
                logger.info(
                    f"Bandit CONVERGED: {exp.experiment_id} → winner='{name}' "
                    f"(prob={prob:.3f}, pulls={exp.total_pulls})"
                )
                return

    async def _persist(self, exp: Experiment, arm_name: str) -> None:
        """Persist arm state to DB (best-effort)."""
        try:
            from shared.db import execute
            arm = exp.arms[arm_name]
            await execute(
                """INSERT INTO bandit_experiments (experiment_id, arm_name, alpha, beta, total_pulls, converged, winner)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (experiment_id, arm_name)
                   DO UPDATE SET alpha = %s, beta = %s, total_pulls = %s, converged = %s, winner = %s, updated_at = NOW()""",
                (exp.experiment_id, arm_name, arm.alpha, arm.beta, arm.total_pulls,
                 exp.converged, exp.winner,
                 arm.alpha, arm.beta, arm.total_pulls, exp.converged, exp.winner),
            )
        except Exception:
            pass  # DB persistence is best-effort

    async def load_from_db(self, experiment_id: str) -> None:
        """Load experiment state from DB."""
        try:
            from shared.db import fetch_all
            rows = await fetch_all(
                "SELECT arm_name, alpha, beta, total_pulls, converged, winner FROM bandit_experiments WHERE experiment_id = %s",
                (experiment_id,),
            )
            if rows:
                exp = self._get_or_create(experiment_id)
                for row in rows:
                    arm = BanditArm(
                        name=row["arm_name"],
                        alpha=row.get("alpha", 1.0),
                        beta=row.get("beta", 1.0),
                        total_pulls=row.get("total_pulls", 0),
                    )
                    exp.arms[row["arm_name"]] = arm
                    exp.total_pulls += arm.total_pulls
                    if row.get("converged"):
                        exp.converged = True
                        exp.winner = row.get("winner", "")
        except Exception:
            pass

    def get_stats(self, experiment_id: str) -> dict:
        """Get experiment statistics."""
        exp = self._experiments.get(experiment_id)
        if not exp:
            return {"experiment_id": experiment_id, "status": "not_found"}
        return {
            "experiment_id": experiment_id,
            "total_pulls": exp.total_pulls,
            "converged": exp.converged,
            "winner": exp.winner,
            "arms": {
                name: {"mean": round(arm.mean_reward, 3), "pulls": arm.total_pulls,
                       "alpha": round(arm.alpha, 1), "beta": round(arm.beta, 1)}
                for name, arm in exp.arms.items()
            },
        }


# Singleton
_bandit: BanditPolicy | None = None


def get_bandit() -> BanditPolicy:
    global _bandit
    if _bandit is None:
        _bandit = BanditPolicy()
    return _bandit
