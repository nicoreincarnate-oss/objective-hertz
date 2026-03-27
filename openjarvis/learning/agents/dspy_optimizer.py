"""DSPy agent optimizer -- programmatic pipeline optimization.

Wraps an agent's reasoning pipeline as a DSPy Module and optimizes
it end-to-end using DSPy teleprompters. Outputs TOML-compatible
config updates written via AgentConfigEvolver.
"""

from __future__ import annotations

import logging
from typing import Any

from openjarvis.core.config import DSPyOptimizerConfig
from openjarvis.core.registry import LearningRegistry
from openjarvis.learning._stubs import AgentLearningPolicy

logger = logging.getLogger(__name__)

# Optional dependency
try:
    import dspy

    HAS_DSPY = True
except ImportError:
    HAS_DSPY = False
    dspy = None  # type: ignore[assignment]


class DSPyAgentOptimizer:
    """Optimize agent configs using DSPy teleprompters.

    Parameters
    ----------
    config:
        DSPyOptimizerConfig controlling optimizer type and parameters.
    """

    def __init__(self, config: DSPyOptimizerConfig) -> None:
        self.config = config

    def optimize(self, trace_store: Any) -> dict[str, Any]:
        """Run DSPy optimization on traces from the store.

        1. Extract traces and convert to DSPy Examples
        2. Build a DSPy Module mirroring the agent pipeline
        3. Run the configured teleprompter
        4. Extract optimized parameters as TOML updates
        5. Write via AgentConfigEvolver if config_dir is set
        """
        # Get traces
        kwargs: dict[str, Any] = {"limit": 10_000}
        if self.config.agent_filter:
            kwargs["agent"] = self.config.agent_filter
        traces = trace_store.list_traces(**kwargs)

        if len(traces) < self.config.min_traces:
            return {
                "status": "skipped",
                "reason": (
                    f"only {len(traces)} traces, "
                    f"min_traces={self.config.min_traces}"
                ),
            }

        if not HAS_DSPY:
            return {
                "status": "error",
                "reason": (
                    "dspy not installed "
                    "(pip install 'openjarvis[learning-dspy]')"
                ),
            }

        try:
            optimized = self._run_dspy_optimization(traces)
        except Exception as exc:
            logger.warning("DSPy optimization failed: %s", exc)
            return {"status": "error", "reason": str(exc)}

        config_updates = self._to_config_updates(optimized)

        # Write configs if config_dir is set
        if self.config.config_dir:
            self._write_configs(config_updates)

        return {
            "status": "completed",
            "traces_used": len(traces),
            "config_updates": config_updates,
        }

    def _run_dspy_optimization(self, traces: list[Any]) -> dict[str, Any]:
        """Run the DSPy teleprompter on converted trace examples."""
        # Convert traces to dspy.Example objects
        examples = []
        for t in traces:
            ex = dspy.Example(
                question=t.query,
                answer=t.result,
            ).with_inputs("question")
            examples.append(ex)

        # Define a simple metric based on trace feedback
        def metric(
            example: Any, prediction: Any, trace: Any = None
        ) -> float:
            for tr in traces:
                if tr.query == example.question:
                    return tr.feedback if tr.feedback is not None else 0.5
            return 0.5

        # Build a minimal DSPy program
        class AgentModule(dspy.Module):
            def __init__(self_inner: Any) -> None:
                super().__init__()
                self_inner.generate = dspy.ChainOfThought(
                    "question -> answer"
                )

            def forward(self_inner: Any, question: str) -> Any:
                return self_inner.generate(question=question)

        program = AgentModule()

        # Select optimizer
        optimizer_name = self.config.optimizer
        if optimizer_name == "BootstrapFewShotWithRandomSearch":
            teleprompter = dspy.BootstrapFewShotWithRandomSearch(
                metric=metric,
                max_bootstrapped_demos=self.config.max_bootstrapped_demos,
                max_labeled_demos=self.config.max_labeled_demos,
                num_candidate_programs=self.config.num_candidate_programs,
            )
        elif optimizer_name == "BootstrapFewShot":
            teleprompter = dspy.BootstrapFewShot(
                metric=metric,
                max_bootstrapped_demos=self.config.max_bootstrapped_demos,
                max_labeled_demos=self.config.max_labeled_demos,
            )
        else:
            teleprompter = dspy.BootstrapFewShot(
                metric=metric,
                max_bootstrapped_demos=self.config.max_bootstrapped_demos,
            )

        # Split data for train/test
        split = max(1, int(len(examples) * 0.8))
        train_set = examples[:split]
        optimized_program = teleprompter.compile(
            program, trainset=train_set
        )

        # Extract optimized parameters
        result: dict[str, Any] = {}
        if hasattr(optimized_program, "generate") and hasattr(
            optimized_program.generate, "demos"
        ):
            result["few_shot_examples"] = [
                {"input": d.question, "output": d.answer}
                for d in optimized_program.generate.demos
                if hasattr(d, "question") and hasattr(d, "answer")
            ]

        return result

    def _to_config_updates(self, optimized: dict[str, Any]) -> dict[str, Any]:
        """Convert DSPy optimization results to TOML-compatible config."""
        updates: dict[str, Any] = {}

        if (
            self.config.optimize_system_prompt
            and "system_prompt" in optimized
        ):
            updates["system_prompt"] = optimized["system_prompt"]

        if (
            self.config.optimize_few_shot
            and "few_shot_examples" in optimized
        ):
            updates["few_shot_examples"] = optimized["few_shot_examples"]

        if (
            self.config.optimize_tool_descriptions
            and "tool_descriptions" in optimized
        ):
            updates["tool_descriptions"] = optimized["tool_descriptions"]

        return updates

    def _write_configs(self, config_updates: dict[str, Any]) -> None:
        """Write updated configs via AgentConfigEvolver, merging with existing."""
        import pathlib

        from openjarvis.learning.agents.agent_evolver import (
            AgentConfigEvolver,
            _write_toml,
        )

        config_dir = pathlib.Path(self.config.config_dir)
        history_dir = config_dir / ".history"
        config_dir.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        agent_name = self.config.agent_filter or "default"
        config_path = config_dir / f"{agent_name}.toml"

        # Load existing TOML data to preserve fields not in config_updates
        existing: dict[str, Any] = {}
        if config_path.exists():
            # Archive before modifying
            evolver = AgentConfigEvolver.__new__(AgentConfigEvolver)
            evolver._config_dir = config_dir
            evolver._history_dir = history_dir
            evolver._archive(agent_name, config_path)

            # Parse existing TOML (simple key=value under [agent])
            import tomllib

            existing = tomllib.loads(config_path.read_text(encoding="utf-8"))

        # Merge: update only the fields present in config_updates
        agent_section = existing.get("agent", {"name": agent_name})
        agent_section.update(config_updates)
        _write_toml(config_path, {"agent": agent_section})


@LearningRegistry.register("dspy")
class _DSPyLearningPolicy(AgentLearningPolicy):
    """Wrapper to register DSPyAgentOptimizer in the LearningRegistry."""

    def __init__(self, **kwargs: object) -> None:
        pass

    def update(self, trace_store: Any, **kwargs: object) -> dict[str, Any]:
        config = DSPyOptimizerConfig()
        optimizer = DSPyAgentOptimizer(config)
        return optimizer.optimize(trace_store)


__all__ = ["DSPyAgentOptimizer"]
