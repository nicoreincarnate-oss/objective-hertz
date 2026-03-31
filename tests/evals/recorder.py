"""Eval result recording with optional DB persistence.

When BEHAVIORAL_EVALS_ENABLED=true, writes to eval_results table.
Otherwise stores in-memory only (tests always run regardless of flag).
"""

from __future__ import annotations

import json
import os


class EvalRecorder:
    """Stores eval results for trend tracking."""

    def __init__(self, db=None):
        self._db = db
        self._results: list[dict] = []

    async def record(
        self,
        suite: str,
        scenario: str,
        passed: bool,
        score: float | None = None,
        cost_usd: float | None = None,
        details: dict | None = None,
    ) -> None:
        result = {
            "suite": suite,
            "scenario": scenario,
            "passed": passed,
            "score": score,
            "cost_usd": cost_usd,
            "details": details or {},
        }
        self._results.append(result)

        if os.environ.get("BEHAVIORAL_EVALS_ENABLED", "").lower() in ("true", "1"):
            await self._persist(result)

    async def _persist(self, result: dict) -> None:
        if self._db is None:
            return
        await self._db.execute(
            """INSERT INTO eval_results (suite, scenario, passed, score, cost_usd, details)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                result["suite"],
                result["scenario"],
                result["passed"],
                result["score"],
                result["cost_usd"],
                json.dumps(result["details"]),
            ),
        )

    @property
    def results(self) -> list[dict]:
        return self._results
