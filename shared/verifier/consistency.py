"""Layer 2: Conditional self-consistency.

Triggered ONLY when:
  1. The call passes Layer 1 (grammar/JSON valid)
  2. AND one of:
     a. Top tool-call token logprob margin < 0.4 (model is uncertain)
     b. Tool schema is marked `ambiguous: true` in routing policy
     c. Operator forced consistency=True via daemon kwargs

When triggered: sample n=3 at temp 0.3, vote on canonical hash, ≥2/3 → return.
Otherwise escalate to next-tier model.

Default trigger rate target ≤15%. If higher, the local model is too uncertain
on this prompt class and we should escalate the whole class permanently.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("perseus.verifier.consistency")


@dataclass
class ConsistencyResult:
    consistent: bool
    chosen_response: str
    vote_count: int
    total_samples: int
    canonical_hashes: list[str]
    confidence: float
    triggered: bool       # False if we skipped consistency check entirely
    attempted_samples: int = 0   # Total samples requested (always = n_samples when triggered)
    successful_samples: int = 0  # Samples that returned without raising an exception
    under_sampled: bool = False  # True when successful_samples < required minimum


CONSISTENCY_LOGPROB_THRESHOLD = 0.4
DEFAULT_N_SAMPLES = 3
DEFAULT_TEMPERATURE = 0.3


class SelfConsistencyChecker:
    """Run a conditional self-consistency check on an LLM response."""

    def __init__(
        self,
        n_samples: int = DEFAULT_N_SAMPLES,
        temperature: float = DEFAULT_TEMPERATURE,
        logprob_threshold: float = CONSISTENCY_LOGPROB_THRESHOLD,
    ):
        self.n_samples = n_samples
        self.temperature = temperature
        self.logprob_threshold = logprob_threshold

    def should_trigger(
        self,
        *,
        logprob_margin: float | None = None,
        ambiguous_schema: bool = False,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        if ambiguous_schema:
            return True
        if logprob_margin is not None and logprob_margin < self.logprob_threshold:
            return True
        return False

    async def check(
        self,
        prompt: str,
        first_response: str,
        *,
        llm_client: Any,
        model: str,
        daemon_name: str,
        pipeline_stage: str = "",
        max_tokens: int = 2000,
        logprob_margin: float | None = None,
        ambiguous_schema: bool = False,
        force: bool = False,
    ) -> ConsistencyResult:
        """Run a self-consistency check, return verdict.

        If trigger conditions aren't met, returns immediately with the first
        response and triggered=False (no extra LLM calls).
        """
        if not self.should_trigger(
            logprob_margin=logprob_margin,
            ambiguous_schema=ambiguous_schema,
            force=force,
        ):
            return ConsistencyResult(
                consistent=True,
                chosen_response=first_response,
                vote_count=1,
                total_samples=1,
                canonical_hashes=[self._canonicalize(first_response)],
                confidence=1.0,
                triggered=False,
                attempted_samples=1,
                successful_samples=1,
                under_sampled=False,
            )

        logger.debug(
            "Self-consistency triggered for %s (logprob=%s ambiguous=%s force=%s)",
            daemon_name, logprob_margin, ambiguous_schema, force,
        )

        # Sample n-1 additional responses. attempted_samples always == n_samples
        # (the first_response counts as attempt #1). successful_samples only counts
        # responses that didn't raise — this is how we prevent silent failure laundering.
        attempted_samples = self.n_samples
        responses = [first_response]
        for i in range(self.n_samples - 1):
            try:
                extra = await llm_client.generate(
                    prompt,
                    model=model,
                    daemon_name=daemon_name,
                    pipeline_stage=f"{pipeline_stage}_consistency_{i+1}",
                    max_tokens=max_tokens,
                    temperature=self.temperature,
                    operation="shared.consistency_sample",
                )
                responses.append(extra)
            except Exception as exc:
                logger.warning("Self-consistency sample %d failed: %s", i + 1, exc)

        return self._vote(responses, attempted_samples=attempted_samples)

    def _canonicalize(self, response: str) -> str:
        """Normalize a response so semantically equal responses produce the same hash."""
        try:
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                obj = json.loads(json_match.group(0))
                canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
                return hashlib.sha256(canonical.encode()).hexdigest()
        except (json.JSONDecodeError, ValueError):
            pass
        cleaned = re.sub(r"\s+", " ", response.strip().lower())
        return hashlib.sha256(cleaned.encode()).hexdigest()

    def _vote(
        self,
        responses: list[str],
        *,
        attempted_samples: int | None = None,
    ) -> ConsistencyResult:
        # attempted_samples reflects how many we TRIED to sample (n_samples).
        # responses reflects how many actually came back without raising.
        # These are distinct — conflating them was P0-9: with 1 success out of 3 attempts,
        # 1/1 = 1.0 >= 2/3 used to evaluate True and launder the failure into a pass.
        successful_samples = len(responses)
        if attempted_samples is None:
            attempted_samples = successful_samples

        # Defensive guard: fewer than 2 successful samples can never constitute a
        # meaningful consistency vote. Force escalation by returning consistent=False.
        if successful_samples < 2:
            logger.warning(
                "Self-consistency under-sampled: %d/%d successful samples "
                "(need >=2). Forcing escalation to prevent silent failure laundering.",
                successful_samples, attempted_samples,
            )
            return ConsistencyResult(
                consistent=False,
                chosen_response=responses[0] if responses else "",
                vote_count=0,
                total_samples=successful_samples,
                canonical_hashes=[self._canonicalize(r) for r in responses],
                confidence=0.0,
                triggered=True,
                attempted_samples=attempted_samples,
                successful_samples=successful_samples,
                under_sampled=True,
            )

        # Require a quorum proportional to what we asked for. For the default n=3
        # this is max(2, 1) = 2. For n=5 this is max(2, 2) = 2. For n=7 this is
        # max(2, 3) = 3. The goal is simply to prevent a single survivor from
        # triggering a pass.
        required_successes = max(2, attempted_samples // 2)
        if successful_samples < required_successes:
            logger.warning(
                "Self-consistency quorum not met: %d successful < %d required "
                "(attempted=%d). Forcing escalation.",
                successful_samples, required_successes, attempted_samples,
            )
            return ConsistencyResult(
                consistent=False,
                chosen_response=responses[0],
                vote_count=0,
                total_samples=successful_samples,
                canonical_hashes=[self._canonicalize(r) for r in responses],
                confidence=0.0,
                triggered=True,
                attempted_samples=attempted_samples,
                successful_samples=successful_samples,
                under_sampled=True,
            )

        hashes = [self._canonicalize(r) for r in responses]
        counter = Counter(hashes)
        winner_hash, winner_count = counter.most_common(1)[0]
        winner_idx = hashes.index(winner_hash)
        winner_response = responses[winner_idx]
        # Key fix: compute majority against attempted_samples, NOT len(responses).
        # This prevents 1/1 from being interpreted as unanimous when 2 samples crashed.
        majority = winner_count / attempted_samples
        return ConsistencyResult(
            consistent=majority >= (2 / 3),
            chosen_response=winner_response,
            vote_count=winner_count,
            total_samples=successful_samples,
            canonical_hashes=hashes,
            confidence=majority,
            triggered=True,
            attempted_samples=attempted_samples,
            successful_samples=successful_samples,
            under_sampled=False,
        )


_default_checker = SelfConsistencyChecker()


async def check_self_consistency(
    prompt: str,
    first_response: str,
    *,
    llm_client: Any,
    model: str,
    daemon_name: str,
    **kwargs: Any,
) -> ConsistencyResult:
    return await _default_checker.check(
        prompt, first_response,
        llm_client=llm_client, model=model, daemon_name=daemon_name, **kwargs,
    )


__all__ = ["SelfConsistencyChecker", "ConsistencyResult", "check_self_consistency"]
