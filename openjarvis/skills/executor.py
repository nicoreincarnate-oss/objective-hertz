"""SkillExecutor — runs skill steps sequentially through ToolExecutor."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.skills.types import SkillManifest
from openjarvis.tools._stubs import ToolExecutor


@dataclass(slots=True)
class SkillResult:
    skill_name: str = ""
    success: bool = True
    step_results: List[ToolResult] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)


class SkillExecutor:
    """Execute a skill manifest step-by-step.

    Each step's arguments_template supports ``{key}`` placeholders
    that are resolved from the context dict (populated by prior step outputs).
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        *,
        bus: Optional[EventBus] = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._bus = bus

    def run(
        self,
        manifest: SkillManifest,
        *,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        """Execute all steps in a skill manifest."""
        ctx: Dict[str, Any] = dict(initial_context or {})
        all_results: List[ToolResult] = []

        if self._bus:
            self._bus.publish(
                EventType.SKILL_EXECUTE_START,
                {"skill": manifest.name, "steps": len(manifest.steps)},
            )

        for i, step in enumerate(manifest.steps):
            # Render template
            try:
                rendered = self._render_template(step.arguments_template, ctx)
            except Exception as exc:
                result = ToolResult(
                    tool_name=step.tool_name,
                    content=f"Template rendering error: {exc}",
                    success=False,
                )
                all_results.append(result)
                break

            # Execute
            tool_call = ToolCall(
                id=f"skill_{manifest.name}_{i}",
                name=step.tool_name,
                arguments=rendered,
            )
            result = self._tool_executor.execute(tool_call)
            all_results.append(result)

            if not result.success:
                break

            # Store output in context
            if step.output_key:
                ctx[step.output_key] = result.content

        success = all(r.success for r in all_results)

        if self._bus:
            self._bus.publish(
                EventType.SKILL_EXECUTE_END,
                {"skill": manifest.name, "success": success},
            )

        return SkillResult(
            skill_name=manifest.name,
            success=success,
            step_results=all_results,
            context=ctx,
        )

    @staticmethod
    def _render_template(template: str, ctx: Dict[str, Any]) -> str:
        """Render {key} placeholders with proper JSON escaping.

        When a placeholder appears inside a JSON string (detected by
        surrounding quotes), the substituted value is JSON-escaped to
        prevent broken JSON from multiline content, quotes, or
        backslashes in tool outputs.
        """
        def _replace(match: re.Match) -> str:
            key = match.group(1)
            val = ctx.get(key, match.group(0))
            if isinstance(val, str):
                # JSON-escape the string value so it's safe inside a
                # JSON string literal.  json.dumps adds surrounding
                # quotes which we strip since the template already has them.
                return json.dumps(val)[1:-1]
            return json.dumps(val)

        return re.sub(r"\{(\w+)\}", _replace, template)


__all__ = ["SkillExecutor", "SkillResult"]
