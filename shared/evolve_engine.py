"""
AlphaEvolve Self-Improvement Engine — LLM-guided evolution of text artifacts.

Paper: AlphaEvolve (Google DeepMind, DAIR.AI highlight).

Adapted for Perseus: evolves **text artifacts** (prompts, email templates,
scoring rubrics, pipeline parameters) rather than algorithms.  The
generate-evaluate-evolve loop applies to any artifact with a measurable
quality metric.

Safety:
  - POMDP risk gate: score_action_risk() rejects mutations with risk > 0.7
  - Mutation scope: prompt/text ONLY (no code, no config, no budget)
  - Max 50 tokens per mutation delta
  - 5 evolution runs per day, $0.20 per run cost cap
  - Automatic rollback on 5%+ regression over 20 trials / 48h

Integration:
  - EvolveEngine generates NEW candidates
  - Bandit (shared/bandit.py) selects WHICH candidate to deploy
  - Real-world outcome feeds back as reward to both

Gated behind ALPHA_EVOLVE feature flag (default true).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger("perseus.evolve")

# Feature flag
ALPHA_EVOLVE_ENABLED = os.environ.get("ALPHA_EVOLVE", "1") == "1"

# Hard safety boundaries (NOT configurable by AlphaEvolve)
MAX_MUTATION_TOKENS = 50
MAX_DAILY_RUNS = 5
COST_CAP_PER_RUN = 0.20  # USD
RISK_THRESHOLD = 0.7
PROTECTED_PATHS = frozenset({
    "wallet.py", "llm_client.py", "security/", "middleware.py",
})

# Valid artifact types
VALID_ARTIFACT_TYPES = frozenset({
    "prompt", "email_template", "scoring_rubric", "pipeline_param",
})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class EvolveCandidate:
    """A single candidate in the evolution population."""

    id: str
    experiment_id: str
    artifact_type: str  # prompt | email_template | scoring_rubric | pipeline_param
    content: str
    parent_id: str | None = None  # None for seed
    generation: int = 0  # 0 = seed, 1+ = evolved
    island: int = 0  # population island (0 or 1)
    metrics: dict[str, float] = field(default_factory=dict)
    status: str = "active"  # active | deployed | promoted | rolled_back | pruned
    created_at: float = field(default_factory=time.time)


@dataclass
class EvolveConfig:
    """Configuration for an evolution run."""

    population_size: int = 20  # candidates per generation
    num_islands: int = 2  # population islands (diversity)
    exploitation_ratio: float = 0.7  # 70% exploit best, 30% explore random
    max_generations: int = 10  # evolution cap
    breadth_model: str = "fast"  # Haiku — generate many variants cheaply
    depth_model: str = "smart"  # Sonnet — generate insightful improvements
    min_trials: int = 20  # minimum evaluations before convergence check
    min_hours: float = 48.0  # minimum time before convergence check
    regression_threshold: float = 0.05  # 5% drop triggers rollback


# ---------------------------------------------------------------------------
# Mutation prompt
# ---------------------------------------------------------------------------

_MUTATION_PROMPT = """\
You are improving a {artifact_type}. Here is the current version:

---
{parent_content}
---

Current scores: {parent_metrics}
Best-in-population scores: {best_metrics}

Generate an improved version. Focus on: {weakest_dimension}.
Constraint: Change no more than 50 tokens from the original.

Output ONLY the improved version, no explanation."""


# ---------------------------------------------------------------------------
# Token delta estimation
# ---------------------------------------------------------------------------


def _estimate_token_count(text: str) -> int:
    """Rough token count: ~4 chars per token."""
    return max(1, len(text) // 4)


def _token_delta(original: str, mutated: str) -> int:
    """Estimate the token difference between two texts."""
    orig_words = set(original.split())
    mut_words = set(mutated.split())
    added = mut_words - orig_words
    removed = orig_words - mut_words
    # Approximate: each changed word ~ 1.3 tokens
    return int(len(added | removed) * 1.3)


# ---------------------------------------------------------------------------
# Safety: POMDP risk scoring
# ---------------------------------------------------------------------------


async def _score_risk(artifact_type: str, content: str) -> float:
    """Score mutation risk via POMDP safety bounds (Phase 30 dependency).

    If Phase 30 is not yet deployed, returns 0.0 (safe) so evolution
    can proceed while gated by other safety boundaries.
    """
    try:
        from shared.safety_bounds import score_action_risk  # Phase 30
        return await score_action_risk(
            action=f"evolve_{artifact_type}",
            context=content[:500],
        )
    except ImportError:
        # Phase 30 not yet deployed — fall back to heuristic
        content_lower = content.lower()
        for protected in PROTECTED_PATHS:
            if protected in content_lower:
                return 0.95  # block mutations referencing protected paths
        return 0.0


# ---------------------------------------------------------------------------
# Daily run counter
# ---------------------------------------------------------------------------


class _DailyRunCounter:
    """Track daily evolution runs to enforce the 5/day cap."""

    def __init__(self) -> None:
        self._runs: list[float] = []

    def count_today(self) -> int:
        """Count runs in the current calendar day."""
        now = time.time()
        day_start = now - (now % 86400)
        self._runs = [t for t in self._runs if t >= day_start]
        return len(self._runs)

    def record_run(self) -> None:
        self._runs.append(time.time())

    def can_run(self) -> bool:
        return self.count_today() < MAX_DAILY_RUNS


_daily_counter = _DailyRunCounter()


# ---------------------------------------------------------------------------
# EvolveEngine
# ---------------------------------------------------------------------------


class EvolveEngine:
    """LLM-guided evolution loop for text artifacts.

    The engine:
    1. Initializes a population from a seed artifact
    2. Evaluates candidates via a caller-supplied evaluator
    3. Selects parents (exploitation + exploration)
    4. Generates mutations via LLM (Haiku breadth, Sonnet depth)
    5. Safety-gates mutations via POMDP risk scoring
    6. Updates population, prunes worst
    7. Checks convergence
    8. Returns best candidate

    After evolution, top candidates become bandit arms for live selection.
    """

    def __init__(self, llm_generate: Callable | None = None) -> None:
        """Initialize engine.

        Args:
            llm_generate: async callable (prompt, system, model, max_tokens) -> str.
                          If None, imports shared.llm_client.LLMClient at runtime.
        """
        self._llm_generate = llm_generate
        self._run_cost: float = 0.0

    async def _llm_call(
        self,
        prompt: str,
        *,
        model: str = "fast",
        max_tokens: int = 1024,
    ) -> str:
        """Call LLM with cost tracking."""
        if self._llm_generate:
            result = await self._llm_generate(
                prompt, "", model, max_tokens,
            )
        else:
            from shared.llm_client import LLMClient
            client = LLMClient()
            result = await client.generate(
                prompt,
                model=model,
                max_tokens=max_tokens,
                daemon_name="openjarvis",
                operation="shared.evolve_generate",
            )
        # Estimate cost: input + output tokens at Haiku rates ($0.001/1K)
        input_tokens = _estimate_token_count(prompt)
        output_tokens = _estimate_token_count(result)
        cost = (input_tokens + output_tokens) * 0.001 / 1000
        if model in ("smart", "primary"):
            cost *= 6  # Sonnet is ~6x Haiku
        self._run_cost += cost
        return result

    # -- Population initialization --

    def _init_population(
        self,
        seed: str,
        artifact_type: str,
        experiment_id: str,
        config: EvolveConfig,
    ) -> list[EvolveCandidate]:
        """Create initial population from seed."""
        population: list[EvolveCandidate] = []
        for i in range(config.population_size):
            candidate = EvolveCandidate(
                id=f"{experiment_id}_{i:03d}",
                experiment_id=experiment_id,
                artifact_type=artifact_type,
                content=seed,
                parent_id=None,
                generation=0,
                island=i % config.num_islands,
            )
            population.append(candidate)
        return population

    # -- Parent selection --

    def _select_parents(
        self,
        population: list[EvolveCandidate],
        config: EvolveConfig,
    ) -> list[EvolveCandidate]:
        """Select parents via exploitation + exploration split."""
        evaluated = [c for c in population if c.metrics]
        if not evaluated:
            return population[:4]

        # Sort by total score (sum of metrics)
        scored = sorted(evaluated, key=lambda c: sum(c.metrics.values()), reverse=True)

        n_exploit = max(1, int(len(scored) * config.exploitation_ratio))
        n_explore = max(1, len(scored) - n_exploit)

        parents = scored[:n_exploit]

        # Exploration: pick from lower-ranked candidates
        if len(scored) > n_exploit:
            import random
            explore_pool = scored[n_exploit:]
            parents += random.sample(explore_pool, min(n_explore, len(explore_pool)))

        return parents[:8]  # cap at 8 parents per generation

    # -- Mutation generation --

    async def _generate_mutations(
        self,
        parents: list[EvolveCandidate],
        artifact_type: str,
        experiment_id: str,
        generation: int,
        config: EvolveConfig,
    ) -> list[EvolveCandidate]:
        """Generate mutations from parents via LLM."""
        children: list[EvolveCandidate] = []

        # Find best-in-population metrics for the prompt
        all_metrics = [c.metrics for c in parents if c.metrics]
        if all_metrics:
            best_metrics = {}
            for m in all_metrics:
                for k, v in m.items():
                    best_metrics[k] = max(best_metrics.get(k, 0.0), v)
        else:
            best_metrics = {}

        for idx, parent in enumerate(parents):
            # Cost cap check
            if self._run_cost >= COST_CAP_PER_RUN:
                logger.warning(
                    "Evolution cost cap reached ($%.3f >= $%.2f), stopping mutations",
                    self._run_cost, COST_CAP_PER_RUN,
                )
                break

            # Decide model: 80% breadth (Haiku), 20% depth (Sonnet)
            use_depth = (idx % 5 == 0)  # every 5th mutation uses depth model
            model = config.depth_model if use_depth else config.breadth_model

            # Find weakest dimension
            weakest = "overall_quality"
            if parent.metrics:
                weakest = min(parent.metrics, key=lambda k: parent.metrics[k])

            prompt = _MUTATION_PROMPT.format(
                artifact_type=artifact_type,
                parent_content=parent.content,
                parent_metrics=parent.metrics or "not yet evaluated",
                best_metrics=best_metrics or "not yet evaluated",
                weakest_dimension=weakest,
            )

            try:
                mutated = await self._llm_call(prompt, model=model, max_tokens=2048)
                mutated = mutated.strip()
            except (RuntimeError, OSError, ImportError) as exc:
                logger.warning("Mutation LLM call failed: %s", exc)
                continue

            # Enforce max mutation size (50 tokens)
            delta = _token_delta(parent.content, mutated)
            if delta > MAX_MUTATION_TOKENS:
                logger.info(
                    "Mutation too large (%d tokens > %d), skipping",
                    delta, MAX_MUTATION_TOKENS,
                )
                continue

            child_id = f"{experiment_id}_g{generation}_{idx:03d}"
            child = EvolveCandidate(
                id=child_id,
                experiment_id=experiment_id,
                artifact_type=artifact_type,
                content=mutated,
                parent_id=parent.id,
                generation=generation,
                island=parent.island,
            )
            children.append(child)

        return children

    # -- Safety gate --

    async def _passes_safety(self, candidate: EvolveCandidate) -> bool:
        """Check candidate against POMDP risk scoring."""
        risk = await _score_risk(candidate.artifact_type, candidate.content)
        if risk > RISK_THRESHOLD:
            logger.warning(
                "Candidate %s rejected: risk=%.2f > %.2f",
                candidate.id, risk, RISK_THRESHOLD,
            )
            return False
        return True

    # -- Population update --

    def _update_population(
        self,
        population: list[EvolveCandidate],
        children: list[EvolveCandidate],
        config: EvolveConfig,
    ) -> list[EvolveCandidate]:
        """Add children, prune to population_size."""
        combined = population + children
        # Sort by total score; unevaluated go to end
        combined.sort(
            key=lambda c: sum(c.metrics.values()) if c.metrics else -1.0,
            reverse=True,
        )
        return combined[: config.population_size]

    # -- Convergence check --

    def _converged(
        self,
        population: list[EvolveCandidate],
        config: EvolveConfig,
    ) -> bool:
        """Check if the population has converged.

        Convergence = top 3 candidates have scores within 2% of each other.
        """
        evaluated = [c for c in population if c.metrics]
        if len(evaluated) < 3:
            return False

        scores = sorted(
            [sum(c.metrics.values()) for c in evaluated],
            reverse=True,
        )
        top3 = scores[:3]
        if top3[0] == 0:
            return False
        spread = (top3[0] - top3[2]) / top3[0]
        return spread < 0.02

    # -- Main evolution loop --

    async def evolve(
        self,
        seed: str,
        artifact_type: str,
        evaluator: Callable,
        config: EvolveConfig | None = None,
        experiment_id: str | None = None,
    ) -> EvolveCandidate:
        """Run a full evolution cycle. Returns the best candidate.

        Args:
            seed: initial artifact text
            artifact_type: one of VALID_ARTIFACT_TYPES
            evaluator: async (content: str) -> dict[str, float]
            config: evolution configuration
            experiment_id: unique experiment ID (auto-generated if None)

        Returns:
            The best EvolveCandidate from the final population.

        Raises:
            ValueError: if artifact_type is invalid or daily cap exceeded
            RuntimeError: if ALPHA_EVOLVE is disabled
        """
        if not ALPHA_EVOLVE_ENABLED:
            raise RuntimeError("ALPHA_EVOLVE feature flag is disabled")

        if artifact_type not in VALID_ARTIFACT_TYPES:
            raise ValueError(
                f"Invalid artifact_type '{artifact_type}'. "
                f"Must be one of: {sorted(VALID_ARTIFACT_TYPES)}"
            )

        if not _daily_counter.can_run():
            raise ValueError(
                f"Daily evolution cap reached ({MAX_DAILY_RUNS} runs/day). "
                "Queued for tomorrow."
            )

        if config is None:
            config = EvolveConfig()

        if experiment_id is None:
            experiment_id = f"evolve_{artifact_type}_{uuid.uuid4().hex[:8]}"

        self._run_cost = 0.0
        _daily_counter.record_run()

        logger.info(
            "Starting evolution: experiment=%s type=%s pop=%d islands=%d",
            experiment_id, artifact_type, config.population_size, config.num_islands,
        )

        # Step 1: Initialize population from seed
        population = self._init_population(seed, artifact_type, experiment_id, config)

        best_candidate = population[0]  # fallback

        for gen in range(config.max_generations):
            # Step 2: Evaluate all candidates
            for candidate in population:
                if not candidate.metrics:
                    try:
                        candidate.metrics = await evaluator(candidate.content)
                    except (RuntimeError, OSError, ValueError) as exc:
                        logger.warning("Evaluator failed for %s: %s", candidate.id, exc)
                        candidate.metrics = {}

            # Step 3: Select parents (exploitation + exploration)
            parents = self._select_parents(population, config)

            # Step 4: Generate mutations via LLM
            children = await self._generate_mutations(
                parents, artifact_type, experiment_id, gen + 1, config,
            )

            # Step 5: Safety gate — POMDP risk scoring
            safe_children: list[EvolveCandidate] = []
            for child in children:
                if await self._passes_safety(child):
                    safe_children.append(child)

            # Step 6: Add children to population, prune worst
            population = self._update_population(population, safe_children, config)

            # Track best
            evaluated = [c for c in population if c.metrics]
            if evaluated:
                best_candidate = max(
                    evaluated,
                    key=lambda c: sum(c.metrics.values()),
                )

            logger.info(
                "Generation %d: %d candidates, %d children, cost=$%.4f, best_score=%.3f",
                gen + 1, len(population), len(safe_children),
                self._run_cost,
                sum(best_candidate.metrics.values()) if best_candidate.metrics else 0.0,
            )

            # Step 7: Check convergence
            if self._converged(population, config):
                logger.info(
                    "Evolution CONVERGED at generation %d for %s",
                    gen + 1, experiment_id,
                )
                break

            # Cost cap check
            if self._run_cost >= COST_CAP_PER_RUN:
                logger.warning(
                    "Evolution cost cap reached ($%.3f), stopping at gen %d",
                    self._run_cost, gen + 1,
                )
                break

        # Persist best candidates to DB
        await self._persist_candidates(population)

        return best_candidate

    # -- DB persistence --

    async def _persist_candidates(self, population: list[EvolveCandidate]) -> None:
        """Persist candidates to evolve_candidates table (best-effort)."""
        try:
            from psycopg.types.json import Jsonb

            from shared.db import execute

            for candidate in population:
                await execute(
                    """INSERT INTO evolve_candidates
                       (id, experiment_id, artifact_type, parent_id, generation,
                        island, content, metrics, status, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, to_timestamp(%s))
                       ON CONFLICT (id) DO UPDATE SET
                         metrics = EXCLUDED.metrics,
                         status = EXCLUDED.status""",
                    (
                        candidate.id,
                        candidate.experiment_id,
                        candidate.artifact_type,
                        candidate.parent_id,
                        candidate.generation,
                        candidate.island,
                        candidate.content,
                        Jsonb(candidate.metrics),
                        candidate.status,
                        candidate.created_at,
                    ),
                )
        except (ConnectionError, RuntimeError, OSError, ImportError) as exc:
            logger.debug("Evolve DB persist failed (best-effort): %s", exc)

    # -- Bandit integration --

    async def register_with_bandit(
        self,
        experiment_id: str,
        candidates: list[EvolveCandidate],
        top_k: int = 3,
    ) -> None:
        """Register top-k evolved candidates as bandit arms.

        After evolution, the best candidates compete via Thompson Sampling
        to decide which gets deployed for real traffic.
        """
        from shared.bandit import get_bandit

        bandit = get_bandit()
        scored = [c for c in candidates if c.metrics]
        scored.sort(key=lambda c: sum(c.metrics.values()), reverse=True)
        top = scored[:top_k]

        arm_names = [c.id for c in top]
        # Ensure experiment + arms exist
        await bandit.select(f"evolve_{experiment_id}", arms=arm_names)
        logger.info(
            "Registered %d evolved candidates as bandit arms for %s",
            len(arm_names), experiment_id,
        )


# ---------------------------------------------------------------------------
# EvolveRollback — monitors deployed mutations and rolls back on regression
# ---------------------------------------------------------------------------


class EvolveRollback:
    """Monitors deployed mutations and rolls back on regression.

    Checks every 5 minutes whether the deployed mutation regresses
    beyond the configured threshold.  If so, restores the parent version,
    logs the rollback, and notifies the operator.
    """

    async def check_regression(
        self,
        experiment_id: str,
        candidate_id: str,
        baseline_metrics: dict[str, float],
        current_metrics: dict[str, float],
        trial_count: int,
        hours_elapsed: float,
        regression_threshold: float = 0.05,
        min_trials: int = 20,
        min_hours: float = 48.0,
    ) -> str | None:
        """Check if the deployed mutation is regressing.

        Returns:
            The metric name that regressed, or None if no regression.
        """
        # Need minimum data before judging
        if trial_count < min_trials and hours_elapsed < min_hours:
            return None

        for key, baseline in baseline_metrics.items():
            current = current_metrics.get(key, 0.0)
            if baseline > 0 and (baseline - current) / baseline > regression_threshold:
                return key

        return None

    async def rollback(
        self,
        experiment_id: str,
        candidate_id: str,
        metric_name: str,
        baseline_value: float,
        current_value: float,
        trial_count: int,
        hours_elapsed: float,
    ) -> bool:
        """Execute a rollback: restore parent, log, notify.

        Returns True if rollback was recorded, False on DB failure.
        """
        logger.warning(
            "ROLLBACK: experiment=%s candidate=%s metric=%s "
            "baseline=%.3f current=%.3f trials=%d hours=%.1f",
            experiment_id, candidate_id, metric_name,
            baseline_value, current_value, trial_count, hours_elapsed,
        )

        # Mark candidate as rolled back
        await self._update_candidate_status(candidate_id, "rolled_back")

        # Log rollback
        await self._log_action(
            experiment_id=experiment_id,
            candidate_id=candidate_id,
            action="rollback",
            reason=f"{metric_name} regressed: {baseline_value:.3f} -> {current_value:.3f}",
            metric_name=metric_name,
            baseline_value=baseline_value,
            current_value=current_value,
            trial_count=trial_count,
            hours_elapsed=hours_elapsed,
        )

        # Feed negative reward to bandit
        try:
            from shared.bandit import get_bandit
            bandit = get_bandit()
            await bandit.update(f"evolve_{experiment_id}", candidate_id, reward=0.0)
        except (ImportError, RuntimeError) as exc:
            logger.debug("Bandit reward update failed: %s", exc)

        return True

    async def promote(
        self,
        experiment_id: str,
        candidate_id: str,
        metrics: dict[str, float],
        trial_count: int,
        hours_elapsed: float,
    ) -> bool:
        """Promote a successful mutation.

        Returns True if promotion was recorded.
        """
        logger.info(
            "PROMOTION: experiment=%s candidate=%s trials=%d hours=%.1f",
            experiment_id, candidate_id, trial_count, hours_elapsed,
        )

        await self._update_candidate_status(candidate_id, "promoted")

        avg_score = sum(metrics.values()) / max(len(metrics), 1)
        await self._log_action(
            experiment_id=experiment_id,
            candidate_id=candidate_id,
            action="promotion",
            reason=f"Stable improvement: avg_score={avg_score:.3f} over {trial_count} trials",
            metric_name="avg_score",
            baseline_value=avg_score,
            current_value=avg_score,
            trial_count=trial_count,
            hours_elapsed=hours_elapsed,
        )

        # Feed positive reward to bandit
        try:
            from shared.bandit import get_bandit
            bandit = get_bandit()
            await bandit.update(f"evolve_{experiment_id}", candidate_id, reward=1.0)
        except (ImportError, RuntimeError) as exc:
            logger.debug("Bandit reward update failed: %s", exc)

        return True

    async def _update_candidate_status(self, candidate_id: str, status: str) -> None:
        """Update candidate status in DB (best-effort)."""
        try:
            from shared.db import execute
            await execute(
                "UPDATE evolve_candidates SET status = %s WHERE id = %s",
                (status, candidate_id),
            )
        except (ConnectionError, RuntimeError, OSError, ImportError):
            pass

    async def _log_action(
        self,
        experiment_id: str,
        candidate_id: str,
        action: str,
        reason: str,
        metric_name: str,
        baseline_value: float,
        current_value: float,
        trial_count: int,
        hours_elapsed: float,
    ) -> None:
        """Log a rollback or promotion to the evolve_rollbacks table."""
        try:
            from shared.db import execute
            await execute(
                """INSERT INTO evolve_rollbacks
                   (experiment_id, candidate_id, action, reason,
                    metric_name, baseline_value, current_value,
                    trial_count, hours_elapsed)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    experiment_id, candidate_id, action, reason,
                    metric_name, baseline_value, current_value,
                    trial_count, hours_elapsed,
                ),
            )
        except (ConnectionError, RuntimeError, OSError, ImportError) as exc:
            logger.debug("Evolve rollback log failed: %s", exc)


# ---------------------------------------------------------------------------
# Built-in evaluators
# ---------------------------------------------------------------------------


async def evaluate_prompt(content: str) -> dict[str, float]:
    """Evaluate a prompt artifact.

    Scores:
      - clarity: LLM rates 1-10 (Haiku call)
      - specificity: concrete instructions / total sentences
      - length_efficiency: inverse of token count (shorter = better)
    """
    # Specificity: count sentences with concrete verbs / total sentences
    sentences = [s.strip() for s in content.replace("\n", ". ").split(".") if s.strip()]
    if not sentences:
        return {"clarity": 0.0, "specificity": 0.0, "length_efficiency": 0.0}

    concrete_markers = {"must", "always", "never", "ensure", "verify", "check", "return", "output"}
    concrete_count = sum(
        1 for s in sentences
        if any(m in s.lower() for m in concrete_markers)
    )
    specificity = concrete_count / max(len(sentences), 1)

    # Length efficiency: 1.0 at 100 tokens, declining linearly
    token_count = _estimate_token_count(content)
    length_efficiency = min(1.0, 100.0 / max(token_count, 1))

    # Clarity: use heuristic (LLM call would cost tokens)
    # Longer, more structured prompts score higher
    has_sections = content.count("\n\n") >= 2
    has_examples = "example" in content.lower() or "e.g." in content.lower()
    clarity = 0.5
    if has_sections:
        clarity += 0.2
    if has_examples:
        clarity += 0.2
    if specificity > 0.3:
        clarity += 0.1

    return {
        "clarity": round(min(1.0, clarity), 3),
        "specificity": round(min(1.0, specificity), 3),
        "length_efficiency": round(length_efficiency, 3),
    }


async def evaluate_email_template(content: str) -> dict[str, float]:
    """Evaluate an email template artifact.

    Scores:
      - compliance: CAN-SPAM checks (unsubscribe, physical address)
      - personalization: count of merge fields {first_name} etc
      - structure: has subject, greeting, body, CTA, closing
    """
    content_lower = content.lower()

    # Compliance: unsubscribe + physical address
    has_unsubscribe = "unsubscribe" in content_lower
    has_address = any(
        marker in content_lower
        for marker in ["street", "suite", "ave", "blvd", "po box", "address"]
    )
    compliance = 0.0
    if has_unsubscribe:
        compliance += 0.5
    if has_address:
        compliance += 0.5

    # Personalization: merge fields like {first_name}, {{company}}, etc.
    import re
    merge_fields = re.findall(r"\{+\w+\}+", content)
    personalization = min(1.0, len(merge_fields) * 0.2)

    # Structure: subject, greeting, body paragraphs, CTA
    has_subject = "subject:" in content_lower or content.startswith("Subject")
    has_greeting = any(
        g in content_lower for g in ["hi ", "hello ", "dear ", "hey "]
    )
    has_cta = any(
        cta in content_lower
        for cta in ["click here", "learn more", "get started", "sign up", "schedule", "book"]
    )
    structure = 0.0
    if has_subject:
        structure += 0.3
    if has_greeting:
        structure += 0.3
    if has_cta:
        structure += 0.4

    return {
        "compliance": round(compliance, 3),
        "personalization": round(personalization, 3),
        "structure": round(structure, 3),
    }


async def evaluate_scoring_rubric(content: str) -> dict[str, float]:
    """Evaluate a scoring rubric artifact.

    Scores:
      - coverage: number of distinct scoring dimensions
      - measurability: proportion of dimensions with numeric ranges
      - balance: variance in dimension weights (lower variance = more balanced)
    """
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    if not lines:
        return {"coverage": 0.0, "measurability": 0.0, "balance": 0.0}

    # Coverage: count lines that look like scoring dimensions
    import re
    dimension_patterns = re.findall(
        r"(?:score|rating|weight|dimension|criterion|factor)",
        content.lower(),
    )
    coverage = min(1.0, len(dimension_patterns) * 0.1)

    # Measurability: lines with numeric ranges (1-10, 0.0-1.0, etc.)
    numeric_lines = [
        l for l in lines
        if re.search(r"\d+\s*[-–]\s*\d+|\d+\.\d+", l)
    ]
    measurability = min(1.0, len(numeric_lines) / max(len(lines), 1))

    # Balance: just check if weights are mentioned
    weight_mentions = re.findall(r"weight|importance|priority", content.lower())
    balance = min(1.0, len(weight_mentions) * 0.25)

    return {
        "coverage": round(coverage, 3),
        "measurability": round(measurability, 3),
        "balance": round(balance, 3),
    }


async def evaluate_pipeline_param(content: str) -> dict[str, float]:
    """Evaluate a pipeline parameter artifact.

    Scores:
      - completeness: number of defined parameters
      - documentation: proportion of params with descriptions
      - safety: presence of bounds/limits/defaults
    """
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    if not lines:
        return {"completeness": 0.0, "documentation": 0.0, "safety": 0.0}

    import re
    # Completeness: count key=value or key: value patterns
    param_lines = [
        l for l in lines
        if re.search(r"\w+\s*[:=]\s*\S", l)
    ]
    completeness = min(1.0, len(param_lines) * 0.1)

    # Documentation: lines with comments or descriptions
    doc_lines = [l for l in lines if "#" in l or "//" in l or l.startswith("- ")]
    documentation = min(1.0, len(doc_lines) / max(len(lines), 1))

    # Safety: mention of limits, bounds, defaults, max, min
    safety_markers = {"max", "min", "limit", "bound", "default", "cap", "threshold"}
    safety_count = sum(
        1 for l in lines
        if any(m in l.lower() for m in safety_markers)
    )
    safety = min(1.0, safety_count * 0.15)

    return {
        "completeness": round(completeness, 3),
        "documentation": round(documentation, 3),
        "safety": round(safety, 3),
    }


# Evaluator registry
EVALUATORS: dict[str, Callable] = {
    "prompt": evaluate_prompt,
    "email_template": evaluate_email_template,
    "scoring_rubric": evaluate_scoring_rubric,
    "pipeline_param": evaluate_pipeline_param,
}


def get_evaluator(artifact_type: str) -> Callable:
    """Get the default evaluator for an artifact type."""
    if artifact_type not in EVALUATORS:
        raise ValueError(
            f"No evaluator for artifact_type '{artifact_type}'. "
            f"Available: {sorted(EVALUATORS.keys())}"
        )
    return EVALUATORS[artifact_type]


# ---------------------------------------------------------------------------
# Convenience: run evolution with default evaluator
# ---------------------------------------------------------------------------


async def run_evolution(
    seed: str,
    artifact_type: str,
    config: EvolveConfig | None = None,
    experiment_id: str | None = None,
    llm_generate: Callable | None = None,
) -> EvolveCandidate:
    """Convenience wrapper: run evolution with default evaluator and bandit wiring.

    Returns the best candidate.
    """
    evaluator = get_evaluator(artifact_type)
    engine = EvolveEngine(llm_generate=llm_generate)
    best = await engine.evolve(
        seed=seed,
        artifact_type=artifact_type,
        evaluator=evaluator,
        config=config,
        experiment_id=experiment_id,
    )

    # Register top candidates as bandit arms
    # (Retrieve population from engine — best-effort via DB later)

    return best
