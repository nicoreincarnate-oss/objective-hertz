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
        # NOTE: Using random module intentionally for statistical sampling (not security).
        # CSPRNG (secrets module) is not needed for Thompson Sampling arm selection.
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
        self._snapshot_loaded: set[str] = set()

    def _get_or_create(self, experiment_id: str, arms: list[str] | None = None) -> Experiment:
        if experiment_id not in self._experiments:
            exp = Experiment(experiment_id=experiment_id)
            for arm_name in (arms or []):
                exp.add_arm(arm_name)
            self._experiments[experiment_id] = exp
        return self._experiments[experiment_id]

    async def _ensure_snapshot_loaded(self, experiment_id: str) -> None:
        """Load persisted snapshot on first access per experiment (lazy init)."""
        if experiment_id in self._snapshot_loaded:
            return
        self._snapshot_loaded.add(experiment_id)
        try:
            restored = await self.load_snapshot(experiment_id)
            if restored:
                logger.debug("Bandit snapshot restored for %s", experiment_id)
        except (ConnectionError, RuntimeError, OSError, ImportError) as exc:
            logger.debug("Bandit snapshot load skipped for %s: %s", experiment_id, exc)

    async def select(self, experiment_id: str, arms: list[str] | None = None) -> str:
        """Select an arm via Thompson Sampling.

        Args:
            experiment_id: unique experiment name
            arms: list of arm names (created on first call)

        Returns: selected arm name
        """
        if not BANDIT_ENABLED:
            return (arms or ["default"])[0]

        await self._ensure_snapshot_loaded(experiment_id)
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
        except (ConnectionError, RuntimeError, OSError, ImportError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            pass  # DB persistence is best-effort

        # Phase 29: persist full experiment snapshot to bandit_state table
        await self._persist_snapshot(exp)

        # Phase 29: emit memory.changed event
        try:
            from shared.comms import MemoryChangedEvent, publish_memory_event
            await publish_memory_event(MemoryChangedEvent(
                source_daemon="shared",
                memory_type="bandit",
                record_id=exp.experiment_id,
                table_name="bandit_state",
                action="update",
                visibility="public",
                summary=f"bandit:{exp.experiment_id} arm={arm_name}",
            ))
        except (ImportError, ConnectionError, RuntimeError, OSError):
            pass  # Event emission is best-effort

    async def _persist_snapshot(self, exp: Experiment) -> None:
        """Persist full experiment state as a JSON snapshot (Phase 29).

        Debounced: only writes at most once per 5 seconds per experiment.
        Uses the bandit_state table with ON CONFLICT upsert.
        """
        import json
        import time as _time

        now = _time.time()
        last = getattr(self, "_last_snapshot_at", {}).get(exp.experiment_id, 0.0)
        if now - last < 5.0:
            return  # debounced

        state = {
            "arms": {
                name: {"alpha": arm.alpha, "beta": arm.beta,
                       "total_pulls": arm.total_pulls, "total_reward": arm.total_reward}
                for name, arm in exp.arms.items()
            },
            "total_pulls": exp.total_pulls,
            "converged": exp.converged,
            "winner": exp.winner,
        }
        state_json = json.dumps(state)

        try:
            from shared.db import execute
            await execute(
                """INSERT INTO bandit_state (bandit_id, state_json, arm_count, total_pulls, updated_at)
                   VALUES (%s, %s, %s, %s, NOW())
                   ON CONFLICT (bandit_id) DO UPDATE
                   SET state_json = EXCLUDED.state_json,
                       arm_count = EXCLUDED.arm_count,
                       total_pulls = EXCLUDED.total_pulls,
                       updated_at = NOW()""",
                (exp.experiment_id, state_json, len(exp.arms), exp.total_pulls),
            )
            if not hasattr(self, "_last_snapshot_at"):
                self._last_snapshot_at: dict[str, float] = {}
            self._last_snapshot_at[exp.experiment_id] = now
        except (ConnectionError, RuntimeError, OSError, ImportError):
            pass  # best-effort

    async def load_snapshot(self, experiment_id: str) -> bool:
        """Load full experiment state from bandit_state table (Phase 29).

        Returns True if state was restored, False if nothing found.
        """
        import json as _json

        try:
            from shared.db import fetch_one
            row = await fetch_one(
                "SELECT state_json FROM bandit_state WHERE bandit_id = %s",
                (experiment_id,),
            )
            if not row:
                return False
            state = _json.loads(row["state_json"])
            exp = self._get_or_create(experiment_id)
            for name, arm_data in state.get("arms", {}).items():
                arm = BanditArm(
                    name=name,
                    alpha=arm_data.get("alpha", 1.0),
                    beta=arm_data.get("beta", 1.0),
                    total_pulls=arm_data.get("total_pulls", 0),
                    total_reward=arm_data.get("total_reward", 0.0),
                )
                exp.arms[name] = arm
            exp.total_pulls = state.get("total_pulls", 0)
            exp.converged = state.get("converged", False)
            exp.winner = state.get("winner", "")
            return True
        except (ConnectionError, RuntimeError, OSError, ImportError, KeyError, ValueError) as exc:
            logger.debug("Bandit snapshot load failed: %s", exc)
            return False

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
        except (ConnectionError, RuntimeError, OSError, ImportError, KeyError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("Bandit DB state load failed: %s", exc)

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


# ---------------------------------------------------------------------------
# Phase 31: UGO Lambda Tuning via Bandit
# ---------------------------------------------------------------------------

# Pre-defined lambda configurations as bandit arms
UGO_LAMBDA_ARMS: dict[str, dict[str, float]] = {
    "default": {"cost": 0.3, "uncertainty": 0.5, "redundancy": 0.8},
    "cost_aggressive": {"cost": 0.6, "uncertainty": 0.3, "redundancy": 0.8},
    "exploration_friendly": {"cost": 0.2, "uncertainty": 0.7, "redundancy": 0.5},
}

UGO_LAMBDA_EXPERIMENT = "ugo_lambda_tuning"


async def select_ugo_lambdas() -> dict:
    """Select UGO lambda weights via Thompson Sampling bandit.

    Returns dict with keys: cost, uncertainty, redundancy.
    Reward signal: task completion rate * (1 / normalized_cost).
    """
    bandit = get_bandit()
    arm_name = await bandit.select(
        UGO_LAMBDA_EXPERIMENT,
        arms=list(UGO_LAMBDA_ARMS.keys()),
    )
    return UGO_LAMBDA_ARMS.get(arm_name, UGO_LAMBDA_ARMS["default"])


async def update_ugo_lambdas(
    arm_name: str,
    task_completed: bool,
    normalized_cost: float,
) -> None:
    """Update the UGO lambda bandit with observed reward.

    Reward = task_completion * (1 / max(normalized_cost, 0.01))
    Capped at 1.0 for the Beta distribution.
    """
    bandit = get_bandit()
    completion_signal = 1.0 if task_completed else 0.0
    cost_efficiency = 1.0 / max(normalized_cost, 0.01)
    reward = min(completion_signal * cost_efficiency, 1.0)
    await bandit.update(UGO_LAMBDA_EXPERIMENT, arm_name, reward)
