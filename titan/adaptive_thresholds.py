"""Adaptive expansion thresholds via Thompson sampling bandits.

Clean-room implementation from Sutton & Barto, Reinforcement Learning (2018),
Chapter 2.7 — Thompson Sampling with Beta-Bernoulli bandits.

Zero HyperAgents code. All algorithms derived from textbook references only.

Feature flag: ENABLE_BANDIT_EXPANSION
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from shared.contracts import ThresholdProvider  # noqa: F401 — runtime_checkable Protocol
from shared.db import execute, fetch_all, fetch_one

logger = logging.getLogger("titan.adaptive_thresholds")

# The 5 expansion thresholds from titan/expansion.py:44-92
THRESHOLD_NAMES: list[str] = [
    "reply_rate_threshold",
    "interest_rate_threshold",
    "proposal_backlog_threshold",
    "uninvoiced_threshold",
    "missing_email_threshold",
]


@dataclass
class BetaBandit:
    """Thompson sampling with Beta distribution.

    Reference: Sutton & Barto, Reinforcement Learning (2018), Ch. 2.7
    Clean-room implementation -- zero HyperAgents code.
    """

    name: str
    alpha: float  # successes + prior
    beta: float  # failures + prior

    def sample(self) -> float:
        """Draw from Beta(alpha, beta) distribution."""
        return float(np.random.beta(self.alpha, self.beta))

    def update(self, reward: float) -> None:
        """Update posterior with binary outcome.

        reward: 1.0 = conversion (success), 0.0 = no conversion (failure)
        """
        self.alpha += reward
        self.beta += 1.0 - reward

    @property
    def mean(self) -> float:
        """Expected value of Beta distribution."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence_interval(self) -> tuple[float, float]:
        """95% credible interval."""
        from scipy.stats import beta as beta_dist

        return (
            float(beta_dist.ppf(0.025, self.alpha, self.beta)),
            float(beta_dist.ppf(0.975, self.alpha, self.beta)),
        )


class AdaptiveThresholds:
    """Manages a set of Thompson sampling bandits for expansion thresholds.

    Implements ThresholdProvider Protocol from shared/contracts.py.
    """

    def __init__(self) -> None:
        self._bandits: dict[str, BetaBandit] = {}

    def get_threshold(self, name: str) -> float:
        """Sample current threshold from bandit posterior.

        Note: For DB-backed operation, use get_threshold_async() instead.
        This sync version works with in-memory bandits only (useful for tests).
        """
        if name not in self._bandits:
            # Default prior -- will be overridden by DB load in async path
            self._bandits[name] = BetaBandit(name=name, alpha=10.0, beta=2.0)
        return self._bandits[name].sample()

    def update(self, name: str, outcome: float) -> None:
        """Update bandit with pipeline outcome (sync, in-memory only).

        For DB-backed operation with meta_evaluations logging, use update_async().
        """
        if name not in self._bandits:
            self._bandits[name] = BetaBandit(name=name, alpha=10.0, beta=2.0)
        self._bandits[name].update(outcome)

    # -- Async DB-backed methods ------------------------------------------

    async def get_threshold_async(self, name: str) -> float:
        """Sample current threshold from bandit posterior (DB-backed)."""
        if name not in self._bandits:
            await self._load_from_db(name)
        return self._bandits[name].sample()

    async def update_async(self, name: str, outcome: float) -> None:
        """Update bandit with pipeline outcome and persist + log."""
        if name not in self._bandits:
            await self._load_from_db(name)
        bandit = self._bandits[name]
        old_value = bandit.mean
        bandit.update(outcome)
        new_value = bandit.mean
        await self._save_to_db(name)
        await self._log_meta_evaluation(
            threshold_name=name,
            old_value=old_value,
            new_value=new_value,
            change_reason="bandit_update",
            confidence=1.0 - (bandit.confidence_interval[1] - bandit.confidence_interval[0]),
            sample_size=int(bandit.alpha + bandit.beta),
        )

    async def _load_from_db(self, name: str) -> None:
        """Load bandit state from DB."""
        row = await fetch_one(
            "SELECT alpha, beta FROM adaptive_thresholds WHERE threshold_name = %s",
            (name,),
        )
        if row:
            self._bandits[name] = BetaBandit(
                name=name,
                alpha=float(row["alpha"]),
                beta=float(row["beta"]),
            )
        else:
            # Fallback: default prior (should not happen after migration)
            logger.warning("Threshold %s not found in DB, using default prior", name)
            self._bandits[name] = BetaBandit(name=name, alpha=10.0, beta=2.0)

    async def _save_to_db(self, name: str) -> None:
        """Persist bandit state to DB."""
        b = self._bandits[name]
        await execute(
            """UPDATE adaptive_thresholds
               SET alpha = %s, beta = %s, current_value = %s,
                   total_updates = total_updates + 1, last_updated = NOW()
               WHERE threshold_name = %s""",
            (b.alpha, b.beta, b.mean, name),
        )

    async def _log_meta_evaluation(
        self,
        *,
        threshold_name: str,
        old_value: float,
        new_value: float,
        change_reason: str,
        confidence: float = 0.0,
        sample_size: int = 0,
        conversion_rate_before: float | None = None,
        conversion_rate_after: float | None = None,
    ) -> None:
        """Log threshold change to meta_evaluations audit table."""
        await execute(
            """INSERT INTO meta_evaluations
               (threshold_name, old_value, new_value, change_reason,
                confidence, sample_size, conversion_rate_before, conversion_rate_after)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                threshold_name,
                round(old_value, 4),
                round(new_value, 4),
                change_reason,
                round(confidence, 3),
                sample_size,
                round(conversion_rate_before, 4) if conversion_rate_before is not None else None,
                round(conversion_rate_after, 4) if conversion_rate_after is not None else None,
            ),
        )

    async def load_all(self) -> None:
        """Load all threshold bandits from DB."""
        rows = await fetch_all(
            "SELECT threshold_name, alpha, beta FROM adaptive_thresholds"
        )
        for row in rows:
            self._bandits[row["threshold_name"]] = BetaBandit(
                name=row["threshold_name"],
                alpha=float(row["alpha"]),
                beta=float(row["beta"]),
            )


# ---------------------------------------------------------------------------
# Training signal collection (ADAPT-03)
# ---------------------------------------------------------------------------


async def collect_training_signal() -> list[tuple[str, float]]:
    """Collect pipeline outcome data as binary training signal.

    Signal: lead conversion (1.0) or non-conversion (0.0)
    Source: leads table -- status transitions over last 7 days
    """
    conversions = await fetch_all(
        """SELECT status, updated_at
           FROM leads
           WHERE updated_at > NOW() - INTERVAL '7 days'
           AND status IN ('converted', 'lost', 'stale')"""
    )

    signals: list[tuple[str, float]] = []
    for lead in conversions:
        outcome = 1.0 if lead["status"] == "converted" else 0.0
        # Map to all active thresholds -- each threshold benefits from
        # overall conversion signal
        for threshold_name in THRESHOLD_NAMES:
            signals.append((threshold_name, outcome))

    return signals


async def run_daily_training() -> int:
    """Daily training job: collect signals and update all bandits.

    Called by Perseus scheduler.
    Returns number of updates applied.
    """
    if os.environ.get("ENABLE_BANDIT_EXPANSION", "").lower() not in ("1", "true"):
        logger.info("Bandit expansion disabled, skipping training")
        return 0

    signals = await collect_training_signal()
    if not signals:
        logger.info("No training signals collected")
        return 0

    thresholds = AdaptiveThresholds()
    await thresholds.load_all()

    for name, outcome in signals:
        await thresholds.update_async(name, outcome)

    logger.info("Applied %d training updates across %d thresholds", len(signals), len(THRESHOLD_NAMES))
    return len(signals)


# ---------------------------------------------------------------------------
# Experiment Manager (ADAPT-05)
# ---------------------------------------------------------------------------


@dataclass
class Experiment:
    """A shadow A/B experiment for a threshold."""

    experiment_id: str
    name: str
    threshold_name: str
    control: BetaBandit
    variant: BetaBandit
    status: str = "active"  # active, completed, cancelled
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    assignments: dict[str, str] = field(default_factory=dict)  # lead_id -> "control"|"variant"


class ExperimentManager:
    """Run multiple shadow experiments simultaneously.

    Experiments compare alternative bandit priors against the production
    bandit (control) using 50/50 random assignment.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}

    async def create_experiment(
        self,
        name: str,
        threshold_name: str,
        variant_alpha: float,
        variant_beta: float,
    ) -> str:
        """Create a shadow experiment with alternative priors.

        Returns experiment_id.
        """
        experiment_id = uuid.uuid4().hex[:12]

        # Load current production bandit as control
        control_row = await fetch_one(
            "SELECT alpha, beta FROM adaptive_thresholds WHERE threshold_name = %s",
            (threshold_name,),
        )
        if control_row:
            control = BetaBandit(
                name=f"{threshold_name}_control",
                alpha=float(control_row["alpha"]),
                beta=float(control_row["beta"]),
            )
        else:
            control = BetaBandit(name=f"{threshold_name}_control", alpha=10.0, beta=2.0)

        variant = BetaBandit(
            name=f"{threshold_name}_variant",
            alpha=variant_alpha,
            beta=variant_beta,
        )

        experiment = Experiment(
            experiment_id=experiment_id,
            name=name,
            threshold_name=threshold_name,
            control=control,
            variant=variant,
        )
        self._experiments[experiment_id] = experiment

        logger.info(
            "Created experiment %s: %s (control=Beta(%.1f,%.1f), variant=Beta(%.1f,%.1f))",
            experiment_id, name,
            control.alpha, control.beta,
            variant_alpha, variant_beta,
        )
        return experiment_id

    def assign_lead(self, lead_id: str) -> dict[str, str]:
        """Assign a lead to control or variant for each active experiment.

        Returns dict of experiment_id -> assignment ("control" or "variant").
        Uses deterministic hashing for consistent assignment.
        """
        assignments: dict[str, str] = {}
        for exp_id, exp in self._experiments.items():
            if exp.status != "active":
                continue
            # Deterministic 50/50 split based on lead_id + experiment_id
            hash_input = f"{lead_id}:{exp_id}".encode()
            hash_val = int(hashlib.sha256(hash_input).hexdigest(), 16)
            assignment = "control" if hash_val % 2 == 0 else "variant"
            exp.assignments[lead_id] = assignment
            assignments[exp_id] = assignment
        return assignments

    async def record_outcome(self, lead_id: str, outcome: float) -> None:
        """Record outcome for all experiments this lead participated in."""
        for _exp_id, exp in self._experiments.items():
            if exp.status != "active":
                continue
            assignment = exp.assignments.get(lead_id)
            if assignment is None:
                continue
            if assignment == "control":
                exp.control.update(outcome)
            else:
                exp.variant.update(outcome)

    async def evaluate_experiments(self, min_samples: int = 50) -> list[dict]:
        """Evaluate all experiments with sufficient data.

        Returns experiments where variant significantly outperforms control.
        Uses Thompson sampling probability: P(variant > control) estimated
        via Monte Carlo draws.
        """
        results: list[dict] = []
        for exp_id, exp in self._experiments.items():
            if exp.status != "active":
                continue

            control_n = int(exp.control.alpha + exp.control.beta - 2)  # subtract initial prior
            variant_n = int(exp.variant.alpha + exp.variant.beta - 2)

            if control_n < min_samples or variant_n < min_samples:
                continue

            # Monte Carlo estimate of P(variant > control)
            n_draws = 10000
            control_samples = np.random.beta(exp.control.alpha, exp.control.beta, n_draws)
            variant_samples = np.random.beta(exp.variant.alpha, exp.variant.beta, n_draws)
            prob_variant_better = float(np.mean(variant_samples > control_samples))

            result = {
                "experiment_id": exp_id,
                "name": exp.name,
                "threshold_name": exp.threshold_name,
                "control_mean": exp.control.mean,
                "variant_mean": exp.variant.mean,
                "control_samples": control_n,
                "variant_samples": variant_n,
                "prob_variant_better": prob_variant_better,
                "significant": prob_variant_better > 0.95 or prob_variant_better < 0.05,
                "winner": "variant" if prob_variant_better > 0.95 else (
                    "control" if prob_variant_better < 0.05 else "inconclusive"
                ),
            }
            results.append(result)

            # If significant, log to meta_evaluations
            if result["significant"]:
                winner_bandit = exp.variant if result["winner"] == "variant" else exp.control
                thresholds = AdaptiveThresholds()
                await thresholds._log_meta_evaluation(
                    threshold_name=exp.threshold_name,
                    old_value=exp.control.mean,
                    new_value=winner_bandit.mean,
                    change_reason="experiment_winner",
                    confidence=prob_variant_better if result["winner"] == "variant" else 1.0 - prob_variant_better,
                    sample_size=control_n + variant_n,
                )
                exp.status = "completed"

        return results

    @property
    def active_experiments(self) -> list[Experiment]:
        """Return all currently active experiments."""
        return [e for e in self._experiments.values() if e.status == "active"]
