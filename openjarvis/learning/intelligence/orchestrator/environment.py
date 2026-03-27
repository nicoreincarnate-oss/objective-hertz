"""RL environment for orchestrator training.

Adapted from IPW's ``environment.py``.  Uses OpenJarvis's
:class:`~openjarvis.tools._stubs.ToolExecutor` for real tool dispatch
(as opposed to IPW's cached-telemetry approach), making it suitable for
both training and evaluation.
"""

from __future__ import annotations

import time

from openjarvis.core.types import ToolCall
from openjarvis.learning.intelligence.orchestrator.types import (
    EpisodeState,
    OrchestratorAction,
    OrchestratorObservation,
)
from openjarvis.tools._stubs import BaseTool, ToolExecutor


class OrchestratorEnvironment:
    """RL environment that executes tools via OpenJarvis ``ToolExecutor``.

    Parameters
    ----------
    tools:
        List of :class:`BaseTool` instances available to the agent.
    max_turns:
        Maximum number of turns per episode.
    """

    def __init__(
        self,
        tools: list[BaseTool],
        max_turns: int = 10,
    ) -> None:
        self._tools = tools
        self._executor = ToolExecutor(tools)
        self._max_turns = max_turns

    def reset(self, task: str) -> EpisodeState:
        """Reset the environment for a new episode.

        Args:
            task: The initial task/question.

        Returns:
            A fresh :class:`EpisodeState`.
        """
        return EpisodeState(initial_prompt=task)

    def step(
        self,
        state: EpisodeState,
        action: OrchestratorAction,
    ) -> tuple[EpisodeState, OrchestratorObservation]:
        """Execute one step: dispatch the tool and observe the result.

        Raises:
            ValueError: If the tool is not available or max turns exceeded.
        """
        # Handle final-answer actions without tool dispatch
        if action.is_final_answer or action.tool_name == "__final_answer__":
            state.final_answer = action.tool_input
            observation = OrchestratorObservation(
                content=action.tool_input,
                latency_seconds=0.0,
                cost_usd=0.0,
                energy_joules=0.0,
                power_watts=0.0,
                tokens=0,
            )
            state.add_turn(action, observation)
            return state, observation

        available = self.get_available_tools()

        if action.tool_name not in available:
            raise ValueError(
                f"Tool '{action.tool_name}' not available. "
                f"Available: {available}"
            )

        if state.num_turns() >= self._max_turns:
            raise ValueError(
                f"Max turns ({self._max_turns}) exceeded"
            )

        # Execute tool via ToolExecutor.
        # Pass tool_input as-is if it looks like JSON, otherwise wrap in
        # the tool's expected input schema (typically {"input": ...}).
        raw_input = action.tool_input
        if raw_input.startswith("{"):
            arguments = raw_input
        else:
            import json
            arguments = json.dumps({"input": raw_input})

        tool_call = ToolCall(
            id=f"orch_{state.num_turns()}",
            name=action.tool_name,
            arguments=arguments,
        )

        t0 = time.time()
        result = self._executor.execute(tool_call)
        latency = time.time() - t0

        observation = OrchestratorObservation(
            content=result.content,
            latency_seconds=latency,
            cost_usd=result.cost_usd,
            energy_joules=0.0,
            power_watts=0.0,
            tokens=result.usage.get("total_tokens", 0),
        )

        state.add_turn(action, observation)
        return state, observation

    def is_done(self, state: EpisodeState) -> bool:
        """Check if the episode is complete."""
        if state.final_answer is not None:
            return True
        if state.num_turns() >= self._max_turns:
            return True
        return False

    def get_available_tools(self) -> list[str]:
        """Return names of all available tools."""
        return [t.spec.name for t in self._tools]


__all__ = ["OrchestratorEnvironment"]
