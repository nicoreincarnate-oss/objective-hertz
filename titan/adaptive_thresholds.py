"""Adaptive threshold management via Thompson sampling bandits.

Clean-room implementation from Sutton & Barto, Reinforcement Learning:
An Introduction (2018), Chapter 2.7 — Thompson Sampling.

Each pipeline threshold is modeled as a Beta-distributed bandit arm.
Observed outcomes (lead conversions) update the posterior, and thresholds
are sampled from the current posterior for stochastic exploration.

Feature flag: ENABLE_BANDIT_EXPANSION
Zero HyperAgents code — all logic derived from textbook references only.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from shared.db import emit_event, execute, fetch_all, fetch_one

logger = logging.getLogger("titan.adaptive_thresholds")

# The 5 thresholds from titan/expansion.py:40-92 (hardcoded originals)
THRESHOLD_NAMES: list[str] = [
    "reply_rate_threshold",
    "interest_rate_threshold",
    "proposal_backlog_threshold",
    "uninvoiced_threshold",
    "missing_email_threshold",
]

# Default warm-start priors: Beta(10, 2) — strong belief current values are good.
# Mean = 10/12 = 0.833.  After ~50 observations, data dominates.
DEFAULT_ALPHA = 10.0
DEFAULT_BETA = 2.0


def _try_confidence_interval(
    alpha: float, beta_val: float
) -> tuple[float, float] | None:
    """Return 95% credible interval if scipy is available, else None."""
    try:
        from scipy.stats import beta as beta_dist

        return (
            float(beta_dist.ppf(0.025, alpha, beta_val)),
            float(beta_dist.ppf(0.975, alpha, beta_val)),
        )
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# BetaBandit — single arm
# ---------------------------------------------------------------------------


@dataclass
class BetaBandit:
    """Thompson sampling with Beta distribution.

    Reference: Sutton & Barto, Reinforcement Learning (2018), Ch. 2.7
    Clean-room implementation — zero HyperAgents code.

    Parameters
    ----------
    name : str
        Identifier for this threshold (matches DB threshold_name).
    alpha : float
        Pseudo-count of successes (prior + observed).
    beta : float
        Pseudo-count of failures (prior + observed).
    """

    name: str
    alpha: float = DEFAULT_ALPHA
    beta: float = DEFAULT_BETA

    def sample(self) -> float:
        """Draw from Beta(alpha, beta) distribution."""
        return float(np.random.beta(self.alpha, self.beta))

    def update(self, reward: float) -> None:
        """Update posterior with binary outcome.

        Parameters
        ----------
        reward : float
            1.0 = conversion (success), 0.0 = no conversion (failure).
        """
        self.alpha += reward
        self.beta += 1.0 - reward

    @property
    def mean(self) -> float:
        """Posterior mean: alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence_interval(self) -> tuple[float, float] | None:
        """95% credible interval (requires scipy, returns None otherwise)."""
        return _try_confidence_interval(self.alpha, self.beta)

    @property
    def total_updates(self) -> int:
        """Approximate number of updates (total pseudo-counts minus priors)."""
        return max(0, int(round(self.alpha + self.beta - DEFAULT_ALPHA - DEFAULT_BETA)))


# ---------------------------------------------------------------------------
# AdaptiveThresholds — implements ThresholdProvider Protocol
# ---------------------------------------------------------------------------


class AdaptiveThresholds:
    """Manages a set of Thompson sampling bandits for expansion thresholds.

    Implements ``ThresholdProvider`` from ``shared/contracts.py``.
    All bandit state is persisted to Postgres (``adaptive_thresholds`` table)
    so that learning survives daemon restarts.

    Every call to ``update()`` logs to ``meta_evaluations`` for audit.
    """

    def __init__(self) -> None:
        self._bandits: dict[str, BetaBandit] = {}

    # -- ThresholdProvider Protocol methods ----------------------------------

    def get_threshold(self, name: str) -> float:
        """Return the current sampled threshold for *name*.

        If the bandit is not yet loaded, returns a default from the
        in-memory cache (no DB hit in sync context).
        """
        if name not in self._bandits:
            # Return default; async load should be called first
            return self._default_for(name)
        return self._bandits[name].sample()

    def update(self, name: str, outcome: float) -> None:
        """Update the bandit for *name* with an observed *outcome*.

        Also logs to meta_evaluations (async logging is fire-and-forget).
        """
        if name not in self._bandits:
            self._bandits[name] = BetaBandit(
                name=name, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA
            )
        bandit = self._bandits[name]
        old_mean = bandit.mean
        bandit.update(outcome)
        new_mean = bandit.mean
        logger.info(
            "Bandit %s updated: outcome=%.1f, mean %.4f -> %.4f",
            name,
            outcome,
            old_mean,
            new_mean,
        )

    # -- Async DB methods (called from pipeline) -----------------------------

    async def load_from_db(self, name: str) -> None:
        """Load bandit state from Postgres."""
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
            self._bandits[name] = BetaBandit(name=name)

    async def load_all_from_db(self) -> None:
        """Load all bandit states from Postgres."""
        rows = await fetch_all(
            "SELECT threshold_name, alpha, beta FROM adaptive_thresholds"
        )
        for row in rows:
            name = row["threshold_name"]
            self._bandits[name] = BetaBandit(
                name=name,
                alpha=float(row["alpha"]),
                beta=float(row["beta"]),
            )

    async def save_to_db(self, name: str) -> None:
        """Persist bandit state to Postgres."""
        if name not in self._bandits:
            return
        b = self._bandits[name]
        await execute(
            """UPDATE adaptive_thresholds
               SET alpha = %s, beta = %s, current_value = %s,
                   total_updates = total_updates + 1, last_updated = NOW()
               WHERE threshold_name = %s""",
            (b.alpha, b.beta, b.mean, name),
        )

    async def log_meta_evaluation(
        self,
        name: str,
        old_value: float,
        new_value: float,
        reason: str = "bandit_update",
        sample_size: int | None = None,
        conversion_rate_before: float | None = None,
        conversion_rate_after: float | None = None,
    ) -> None:
        """Log a threshold change to meta_evaluations for audit."""
        bandit = self._bandits.get(name)
        confidence = bandit.mean if bandit else None
        await execute(
            """INSERT INTO meta_evaluations
               (threshold_name, old_value, new_value, change_reason,
                confidence, sample_size, conversion_rate_before, conversion_rate_after)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                name,
                old_value,
                new_value,
                reason,
                confidence,
                sample_size,
                conversion_rate_before,
                conversion_rate_after,
            ),
        )

    async def update_and_persist(self, name: str, outcome: float) -> None:
        """Update bandit, persist to DB, and log to meta_evaluations."""
        if name not in self._bandits:
            await self.load_from_db(name)

        bandit = self._bandits[name]
        old_mean = bandit.mean
        bandit.update(outcome)
        new_mean = bandit.mean

        await self.save_to_db(name)
        await self.log_meta_evaluation(
            name=name,
            old_value=old_mean,
            new_value=new_mean,
            reason="bandit_update",
        )

    # -- Async threshold sampling (for pipeline use) -------------------------

    async def get_threshold_async(self, name: str) -> float:
        """Load from DB if needed, then sample."""
        if name not in self._bandits:
            await self.load_from_db(name)
        return self._bandits[name].sample()

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _default_for(name: str) -> float:
        """Return the original hardcoded threshold as fallback."""
        defaults = {
            "reply_rate_threshold": 1.5,
            "interest_rate_threshold": 12.0,
            "proposal_backlog_threshold": 3.0,
            "uninvoiced_threshold": 2.0,
            "missing_email_threshold": 10.0,
        }
        return defaults.get(name, 0.5)

    def get_bandit(self, name: str) -> BetaBandit | None:
        """Return the bandit for *name* if loaded."""
        return self._bandits.get(name)

    def loaded_names(self) -> list[str]:
        """Return names of all loaded bandits."""
        return list(self._bandits.keys())


# ---------------------------------------------------------------------------
# Pipeline outcome training signal (ADAPT-03)
# ---------------------------------------------------------------------------


async def collect_training_signal() -> list[tuple[str, float]]:
    """Collect pipeline outcome data as binary training signal.

    Signal: lead conversion (1.0) or non-conversion (0.0).
    Source: clients table -- status transitions over last 7 days.

    Returns a list of (threshold_name, outcome) pairs suitable for
    feeding into AdaptiveThresholds.update_and_persist().
    """
    rows = await fetch_all(
        """SELECT status, updated_at
           FROM clients
           WHERE updated_at > NOW() - INTERVAL '7 days'
             AND status IN (
                 'closed', 'building', 'deployed', 'invoiced', 'paid',
                 'lost', 'stale', 'unresponsive'
             )"""
    )

    converted_statuses = {"closed", "building", "deployed", "invoiced", "paid"}
    signals: list[tuple[str, float]] = []

    for row in rows:
        outcome = 1.0 if row["status"] in converted_statuses else 0.0
        for threshold_name in THRESHOLD_NAMES:
            signals.append((threshold_name, outcome))

    logger.info(
        "Collected %d training signals from %d pipeline outcomes",
        len(signals),
        len(rows),
    )
    return signals


async def run_daily_training() -> int:
    """Daily training job: collect signals and update all bandits.

    Designed to be called from Perseus scheduler.
    Returns the number of signals processed.
    """
    if os.environ.get("ENABLE_BANDIT_EXPANSION", "").lower() not in ("1", "true"):
        logger.info("Bandit expansion disabled, skipping daily training")
        return 0

    signals = await collect_training_signal()
    if not signals:
        logger.info("No training signals available")
        return 0

    thresholds = AdaptiveThresholds()
    await thresholds.load_all_from_db()

    for name, outcome in signals:
        await thresholds.update_and_persist(name, outcome)

    await emit_event(
        "adaptive_threshold_training",
        {"signals_processed": len(signals), "thresholds_updated": len(THRESHOLD_NAMES)},
    )

    return len(signals)


# ---------------------------------------------------------------------------
# ExperimentManager — concurrent A/B shadow experiments (ADAPT-05)
# ---------------------------------------------------------------------------


@dataclass
class Experiment:
    """A single A/B experiment comparing control vs variant thresholds."""

    id: int
    name: str
    threshold_name: str
    variant_alpha: float
    variant_beta: float
    status: str = "active"  # active, concluded, cancelled
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ExperimentManager:
    """Run multiple shadow A/B experiments simultaneously.

    Each experiment compares the current (control) bandit against a
    variant with different priors.  Leads are randomly assigned 50/50.
    After sufficient sample size, experiments are evaluated statistically.
    """

    MIN_SAMPLE_SIZE = 30  # minimum per arm before evaluation

    async def create_experiment(
        self,
        name: str,
        threshold_name: str,
        variant_alpha: float,
        variant_beta: float,
    ) -> int:
        """Create a new shadow experiment.

        Returns the experiment ID.
        """

        row = await fetch_one(
            """INSERT INTO meta_evaluations
               (threshold_name, old_value, new_value, change_reason,
                confidence, sample_size)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                threshold_name,
                variant_alpha,
                variant_beta,
                f"experiment_created:{name}",
                0.0,
                0,
            ),
        )
        experiment_id = row["id"] if row else 0

        await emit_event(
            "adaptive_experiment_created",
            {
                "experiment_id": experiment_id,
                "name": name,
                "threshold_name": threshold_name,
                "variant_alpha": variant_alpha,
                "variant_beta": variant_beta,
            },
        )

        logger.info(
            "Created experiment %d: %s (threshold=%s, variant=Beta(%.1f, %.1f))",
            experiment_id,
            name,
            threshold_name,
            variant_alpha,
            variant_beta,
        )
        return experiment_id

    async def assign_lead(self, lead_id: str) -> dict[str, str]:
        """Assign a lead to control or variant for each active experiment.

        Uses simple 50/50 random assignment (no stratification needed
        for shadow experiments).

        Returns dict mapping experiment_name -> "control" | "variant".
        """
        experiments = await fetch_all(
            """SELECT id, threshold_name, old_value, new_value, change_reason
               FROM meta_evaluations
               WHERE change_reason LIKE 'experiment_created:%'
                 AND sample_size = 0"""
        )

        assignments: dict[str, str] = {}
        for exp in experiments:
            name = exp["change_reason"].replace("experiment_created:", "")
            arm = "variant" if random.random() < 0.5 else "control"
            assignments[name] = arm

        return assignments

    async def record_outcome(
        self, experiment_name: str, arm: str, outcome: float
    ) -> None:
        """Record an outcome for a specific experiment arm.

        Stores results in meta_evaluations for later analysis.
        """
        await execute(
            """INSERT INTO meta_evaluations
               (threshold_name, old_value, new_value, change_reason,
                confidence, sample_size)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                f"exp:{experiment_name}",
                outcome if arm == "control" else 0.0,
                outcome if arm == "variant" else 0.0,
                f"experiment_outcome:{arm}",
                outcome,
                1,
            ),
        )

    async def evaluate_experiments(self) -> list[dict]:
        """Evaluate all experiments with sufficient data.

        For each experiment, compare mean conversion rates of control vs
        variant arms.  Returns experiments where variant outperforms control.
        """
        # Find active experiments
        experiments = await fetch_all(
            """SELECT DISTINCT change_reason
               FROM meta_evaluations
               WHERE change_reason LIKE 'experiment_created:%'"""
        )

        results: list[dict] = []
        for exp_row in experiments:
            name = exp_row["change_reason"].replace("experiment_created:", "")

            # Gather control outcomes
            control_rows = await fetch_all(
                """SELECT confidence AS outcome
                   FROM meta_evaluations
                   WHERE threshold_name = %s
                     AND change_reason = 'experiment_outcome:control'""",
                (f"exp:{name}",),
            )
            # Gather variant outcomes
            variant_rows = await fetch_all(
                """SELECT confidence AS outcome
                   FROM meta_evaluations
                   WHERE threshold_name = %s
                     AND change_reason = 'experiment_outcome:variant'""",
                (f"exp:{name}",),
            )

            control_outcomes = [float(r["outcome"]) for r in control_rows]
            variant_outcomes = [float(r["outcome"]) for r in variant_rows]

            n_control = len(control_outcomes)
            n_variant = len(variant_outcomes)

            if n_control < self.MIN_SAMPLE_SIZE or n_variant < self.MIN_SAMPLE_SIZE:
                results.append(
                    {
                        "name": name,
                        "status": "insufficient_data",
                        "n_control": n_control,
                        "n_variant": n_variant,
                        "min_required": self.MIN_SAMPLE_SIZE,
                    }
                )
                continue

            mean_control = sum(control_outcomes) / n_control if n_control else 0.0
            mean_variant = sum(variant_outcomes) / n_variant if n_variant else 0.0

            # Simple comparison: variant wins if mean is higher
            winner = "variant" if mean_variant > mean_control else "control"
            lift = (
                (mean_variant - mean_control) / mean_control * 100
                if mean_control > 0
                else 0.0
            )

            result = {
                "name": name,
                "status": "concluded",
                "n_control": n_control,
                "n_variant": n_variant,
                "mean_control": round(mean_control, 4),
                "mean_variant": round(mean_variant, 4),
                "winner": winner,
                "lift_percent": round(lift, 2),
            }
            results.append(result)

            # Log conclusion
            await self.log_meta_evaluation(
                name=name,
                mean_control=mean_control,
                mean_variant=mean_variant,
                winner=winner,
                n_control=n_control,
                n_variant=n_variant,
            )

        return results

    async def log_meta_evaluation(
        self,
        name: str,
        mean_control: float,
        mean_variant: float,
        winner: str,
        n_control: int,
        n_variant: int,
    ) -> None:
        """Log experiment conclusion to meta_evaluations."""
        await execute(
            """INSERT INTO meta_evaluations
               (threshold_name, old_value, new_value, change_reason,
                confidence, sample_size, conversion_rate_before, conversion_rate_after)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                f"exp:{name}",
                mean_control,
                mean_variant,
                f"experiment_winner:{winner}",
                abs(mean_variant - mean_control),
                n_control + n_variant,
                mean_control,
                mean_variant,
            ),
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """Check if adaptive thresholds are enabled via feature flag."""
    return os.environ.get("ENABLE_BANDIT_EXPANSION", "").lower() in ("1", "true")
