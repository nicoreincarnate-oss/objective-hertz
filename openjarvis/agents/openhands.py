"""OpenHandsAgent -- wraps the real openhands-sdk for repo/code execution.

Requires the ``openhands-sdk`` package (``uv sync --extra openhands``).
For the native CodeAct-style agent, see :mod:`openjarvis.agents.native_openhands`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry
from openjarvis.engine._stubs import InferenceEngine


@AgentRegistry.register("openhands")
class OpenHandsAgent(BaseAgent):
    """Agent that wraps the real openhands-sdk package.

    This is a thin adapter that delegates to the ``openhands-sdk``
    library for AI-driven software development tasks.  Requires
    ``openhands-sdk`` to be installed.
    """

    agent_id = "openhands"

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        *,
        bus: EventBus | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        workspace: str | None = None,
        api_key: str | None = None,
    ) -> None:
        resolved_model = self._resolve_model(model)
        super().__init__(
            engine, resolved_model, bus=bus,
            temperature=temperature, max_tokens=max_tokens,
        )
        self._workspace = self._resolve_workspace(workspace)
        self._api_key = self._resolve_api_key(api_key)

    @staticmethod
    def _clean_env_value(value: str | None) -> str:
        if not value:
            return ""
        cleaned = value.strip()
        if not cleaned or cleaned.startswith("CHANGE_ME"):
            return ""
        return cleaned

    @classmethod
    def _resolve_workspace(cls, workspace: str | None) -> str:
        candidate = (
            cls._clean_env_value(workspace)
            or cls._clean_env_value(os.environ.get("OPENHANDS_WORKSPACE"))
            or cls._clean_env_value(os.environ.get("JARVIS_WORKSPACE"))
            or os.getcwd()
        )
        return str(Path(candidate).expanduser().resolve())

    @staticmethod
    def _resolve_api_key(api_key: str | None) -> str:
        for key in (
            api_key,
            os.environ.get("OPENHANDS_API_KEY"),
            os.environ.get("LLM_API_KEY"),
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("OPENROUTER_API_KEY"),
            os.environ.get("GEMINI_API_KEY"),
            os.environ.get("GOOGLE_API_KEY"),
        ):
            cleaned = OpenHandsAgent._clean_env_value(key)
            if cleaned:
                return cleaned
        return ""

    @staticmethod
    def _resolve_model(model: str | None) -> str:
        candidate = (
            OpenHandsAgent._clean_env_value(model)
            or OpenHandsAgent._clean_env_value(os.environ.get("OPENHANDS_MODEL"))
            or OpenHandsAgent._clean_env_value(os.environ.get("LLM_MODEL"))
        )
        provider = OpenHandsAgent._clean_env_value(
            os.environ.get("OPENHANDS_PROVIDER"),
        )
        if not candidate:
            return ""
        if "/" in candidate:
            return candidate
        if provider:
            return f"{provider}/{candidate}"
        return ""

    def set_workspace(self, workspace: str) -> None:
        """Update the working directory used by the OpenHands conversation."""
        self._workspace = str(Path(workspace).expanduser().resolve())

    @staticmethod
    def _extract_content(conversation: Any) -> str:
        """Best-effort extraction of the latest assistant content."""

        def _coerce_text(obj: Any) -> str:
            if obj is None:
                return ""
            if isinstance(obj, str):
                return obj.strip()
            if isinstance(obj, dict):
                for key in ("content", "text", "message", "response", "summary"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                nested = obj.get("message")
                if isinstance(nested, dict):
                    return _coerce_text(nested)
                if isinstance(nested, str):
                    return nested.strip()
                return ""
            for attr in ("content", "text", "message", "response", "summary"):
                value = getattr(obj, attr, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if value is not None and value is not obj:
                    nested = _coerce_text(value)
                    if nested:
                        return nested
            return ""

        candidates: list[Any] = []
        if hasattr(conversation, "get_messages"):
            try:
                candidates = list(conversation.get_messages())
            except Exception:
                candidates = []

        if not candidates:
            state = getattr(conversation, "state", None)
            if state is not None:
                for attr in ("messages", "events", "history"):
                    items = getattr(state, attr, None)
                    if items:
                        try:
                            candidates = list(items)
                        except TypeError:
                            candidates = [items]
                        break

        for item in reversed(candidates):
            content = _coerce_text(item)
            if content:
                return content

            if isinstance(item, dict):
                role = item.get("role") or item.get("sender")
                if role in {"assistant", "agent"}:
                    content = _coerce_text(item.get("content") or item.get("message"))
                    if content:
                        return content
            else:
                role = getattr(item, "role", None) or getattr(item, "sender", None)
                if role in {"assistant", "agent"}:
                    content = _coerce_text(
                        getattr(item, "content", None)
                        or getattr(item, "message", None)
                    )
                    if content:
                        return content

        return ""

    def run(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        try:
            from opentelemetry.semconv_ai import SpanAttributes  # type: ignore[import-untyped]

            if not hasattr(SpanAttributes, "LLM_USAGE_TOTAL_TOKENS") and hasattr(
                SpanAttributes,
                "GEN_AI_USAGE_TOTAL_TOKENS",
            ):
                SpanAttributes.LLM_USAGE_TOTAL_TOKENS = SpanAttributes.GEN_AI_USAGE_TOTAL_TOKENS
        except Exception:
            pass

        try:
            from openhands.sdk import Agent, Conversation, LLM  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "OpenHandsAgent requires the openhands-sdk package. "
                "Install it with: uv sync --extra openhands"
            ) from None

        self._emit_turn_start(input)

        if not self._model:
            self._emit_turn_end(turns=0, error=True)
            raise RuntimeError(
                "OpenHandsAgent needs a model. Set the agent model or "
                "OPENHANDS_MODEL/LLM_MODEL to a LiteLLM-style value such as "
                "'anthropic/claude-sonnet-4-5-20250929'."
            )

        conversation = None
        try:
            llm_kwargs: dict[str, Any] = {"model": self._model}
            if self._api_key:
                llm_kwargs["api_key"] = self._api_key
            llm = LLM(**llm_kwargs)
            agent = Agent(llm=llm)
            conversation = Conversation(agent=agent, workspace=self._workspace)
            conversation.send_message(input)
            conversation.run()
            content = self._extract_content(conversation)
            self._emit_turn_end(turns=1, content_length=len(content))
            return AgentResult(
                content=content,
                turns=1,
                metadata={
                    "workspace": self._workspace,
                    "model": self._model,
                },
            )
        except Exception as exc:
            self._emit_turn_end(turns=1, error=True)
            return AgentResult(
                content=f"OpenHands agent failed: {exc}",
                turns=1,
                metadata={
                    "error": True,
                    "workspace": self._workspace,
                    "model": self._model,
                },
            )
        finally:
            if conversation is not None:
                close = getattr(conversation, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass


__all__ = ["OpenHandsAgent"]
