"""OperativeAgent — persistent, scheduled agent for autonomous operation.

Extends ToolUsingAgent with built-in session persistence and state recall.
Designed for Operators: autonomous agents that run on a schedule with
automatic state management between ticks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall, ToolResult
from openjarvis.engine._stubs import InferenceEngine
from openjarvis.tools._stubs import BaseTool
from shared.capability_router import score_actions
from shared.cost_events import UnifiedBudget, create_default_budget

logger = logging.getLogger(__name__)


@AgentRegistry.register("operative")
class OperativeAgent(ToolUsingAgent):
    """Persistent autonomous agent with built-in state management.

    The Operative agent extends the standard tool-calling loop with:

    1. **Session loading** — restores conversation history from previous ticks.
    2. **State recall** — retrieves previous state JSON from memory backend.
    3. **System prompt** — injects the operator's protocol instructions.
    4. **Tool loop** — standard function-calling loop (same as Orchestrator).
    5. **Session save** — persists the tick's prompt and response.
    6. **State persistence** — auto-persists state if the agent didn't do it
       explicitly via memory_store tool.
    """

    agent_id = "operative"
    accepts_tools = True

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        *,
        tools: list[BaseTool] | None = None,
        bus: EventBus | None = None,
        max_turns: int = 20,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        system_prompt: str | None = None,
        operator_id: str | None = None,
        session_store: Any | None = None,
        memory_backend: Any | None = None,
        interactive: bool = False,
        confirm_callback=None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            engine, model, tools=tools, bus=bus,
            max_turns=max_turns, temperature=temperature,
            max_tokens=max_tokens,
            interactive=interactive, confirm_callback=confirm_callback,
        )
        self._system_prompt = system_prompt or ""
        self._operator_id = operator_id
        self._session_store = session_store
        self._memory_backend = memory_backend

        # Task 18b-03: Cached stable prefix for prompt cache hits.
        # The stable portion (daemon identity, tool definitions, safety rules)
        # is rendered ONCE and reused across ticks so the LLM provider's
        # prompt cache sees an identical prefix on every request.
        self._cached_stable_prefix: str | None = None

        # Phase 31: BATS budget tracker — injected into context after each tool call
        self._budget: UnifiedBudget = kwargs.get("budget") or create_default_budget()

    def run(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Execute a single operator tick.

        When ANATOMY_GENERATOR_LOOP is enabled, delegates to run_iter()
        via drain_agent_loop() for backward compatibility.
        """
        if self._is_generator_loop_enabled():
            return self._run_via_generator(input, context, **kwargs)
        return self._run_sync(input, context, **kwargs)

    def _run_via_generator(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Run using the generator loop, returning an AgentResult."""
        from shared.agent_loop import TerminalReason, drain_agent_loop

        async def _drain():
            gen = self.run_iter(input, context, **kwargs)
            return await drain_agent_loop(gen)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _drain())
                tick = future.result()
        else:
            tick = asyncio.run(_drain())

        metadata: dict[str, Any] = {"tokens_used": tick.tokens_used}
        if tick.terminal and tick.terminal != TerminalReason.COMPLETED:
            metadata["terminal_reason"] = tick.terminal.value
            metadata["terminal_message"] = tick.terminal_message

        return AgentResult(
            content=tick.content,
            tool_results=[],
            turns=tick.turn_number + 1,
            metadata=metadata,
        )

    def _run_sync(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Original synchronous operative tick — preserved as-is."""
        self._emit_turn_start(input)

        # Task 18b-03: Build system prompt with stable-prefix caching.
        # The stable portion (daemon identity, tool definitions, safety
        # rules) is rendered ONCE and reused across ticks.  Only the
        # volatile suffix (previous state) changes per tick.
        system_prompt = self._build_cached_system_prompt()

        # 3. Load session history
        session_messages = self._load_session()

        # 4. Build messages
        messages = self._build_operative_messages(
            input, context, system_prompt=system_prompt,
            session_messages=session_messages,
        )

        # 5. Run function-calling tool loop
        openai_tools = self._executor.get_openai_tools() if self._tools else []
        all_tool_results: list[ToolResult] = []
        turns = 0
        content = ""
        state_stored_by_tool = False
        total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        # UGO: action history and tool-call dedup hashes
        ugo_action_history: list[str] = []
        ugo_tool_hashes: set[str] = set()

        for _turn in range(self._max_turns):
            turns += 1

            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            gen_kwargs: dict[str, Any] = {}
            if openai_tools:
                gen_kwargs["tools"] = openai_tools

            result = self._generate(messages, **gen_kwargs)
            usage = result.get("usage", {})
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)
            content = result.get("content", "")
            raw_tool_calls = result.get("tool_calls", [])

            if not raw_tool_calls:
                content = self._check_continuation(result, messages)
                break

            # UGO: evaluate utility before executing tool calls
            if self._is_ugo_enabled():
                confidence = self._budget.remaining_pct / 100.0
                ugo_scores = score_actions(
                    current_confidence=confidence,
                    budget=self._budget,
                    action_history=ugo_action_history,
                    tool_call_hashes=ugo_tool_hashes,
                )
                top_action = ugo_scores[0].action if ugo_scores else "tool_call"
                logger.debug(
                    "UGO: top_action=%s utility=%.3f (confidence=%.2f, turn=%d)",
                    top_action,
                    ugo_scores[0].utility if ugo_scores else 0.0,
                    confidence,
                    turns,
                )
                if top_action in ("respond", "stop"):
                    ugo_action_history.append(top_action)
                    # UGO advises to stop — treat current content as final
                    break
                if top_action == "verify":
                    ugo_action_history.append("verify")
                    # Continue with tool execution but flag verification needed
                    content = content or ""
                ugo_action_history.append("tool_call")

            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", "{}"),
                )
                for i, tc in enumerate(raw_tool_calls)
            ]

            messages.append(Message(
                role=Role.ASSISTANT,
                content=content,
                tool_calls=tool_calls,
            ))

            for tc in tool_calls:
                # Loop guard check
                if self._loop_guard:
                    verdict = self._loop_guard.check_call(tc.name, tc.arguments)
                    if verdict.blocked:
                        tool_result = ToolResult(
                            tool_name=tc.name,
                            content=f"Loop guard: {verdict.reason}",
                            success=False,
                        )
                        all_tool_results.append(tool_result)
                        messages.append(Message(
                            role=Role.TOOL,
                            content=tool_result.content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))
                        continue

                tool_result = self._executor.execute(tc)
                all_tool_results.append(tool_result)

                # Phase 31: Record tool use in BATS budget tracker
                self._budget.record_tool_use(tc.name)

                # UGO: track tool call hash for redundancy detection
                ugo_tool_hashes.add(f"{tc.name}:{tc.arguments}")

                # Track if agent stored state via memory_store
                if tc.name == "memory_store" and self._operator_id:
                    try:
                        args = json.loads(tc.arguments)
                        state_key = f"operator:{self._operator_id}:state"
                        if args.get("key", "") == state_key:
                            state_stored_by_tool = True
                    except (json.JSONDecodeError, TypeError):
                        pass

                messages.append(Message(
                    role=Role.TOOL,
                    content=tool_result.content,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

                # Phase 31: Inject compact budget status after tool result
                if self._is_bats_enabled():
                    messages.append(Message(
                        role=Role.SYSTEM,
                        content=self._budget.format_status(),
                    ))
        else:
            # Max turns exceeded
            self._save_session(input, content)
            meta = dict(total_usage)
            meta["max_turns_exceeded"] = True
            return AgentResult(
                content=content or "Maximum turns reached without a final answer.",
                tool_results=all_tool_results,
                turns=turns,
                metadata=meta,
            )

        # 6. Save session
        self._save_session(input, content)

        # 7. Auto-persist state if agent didn't do it explicitly
        if not state_stored_by_tool:
            self._auto_persist_state(content)

        self._emit_turn_end(turns=turns, content_length=len(content))
        return AgentResult(
            content=content,
            tool_results=all_tool_results,
            turns=turns,
            metadata=total_usage,
        )

    # ------------------------------------------------------------------
    # Generator-based loop (Phase 26b-03, gated by ANATOMY_GENERATOR_LOOP)
    # ------------------------------------------------------------------

    def _is_generator_loop_enabled(self) -> bool:
        return os.environ.get("ANATOMY_GENERATOR_LOOP", "").lower() in ("true", "1")

    @staticmethod
    def _is_bats_enabled() -> bool:
        return os.environ.get("BATS_ADAPTIVE_BUDGET", "true").lower() in ("true", "1", "yes")

    @staticmethod
    def _is_ugo_enabled() -> bool:
        return os.environ.get("UGO_UTILITY_ROUTING", "true").lower() in ("true", "1", "yes")

    async def run_iter(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator:
        """Generator-based operative loop — yields TickResult per turn.

        Preserves session/state lifecycle (load/save) around the generator core.
        Gated behind ANATOMY_GENERATOR_LOOP.
        """
        from shared.agent_loop import (
            agent_loop,
        )

        if not self._is_generator_loop_enabled():
            raise NotImplementedError("Generator loop requires ANATOMY_GENERATOR_LOOP=true")

        self._emit_turn_start(input)

        # Task 18b-03: Build system prompt with stable-prefix caching
        system_prompt = self._build_cached_system_prompt()

        # 3. Load session history
        session_messages = self._load_session()

        # 4. Build messages
        messages = self._build_operative_messages(
            input, context, system_prompt=system_prompt,
            session_messages=session_messages,
        )

        openai_tools = self._executor.get_openai_tools() if self._tools else []
        all_tool_results: list[ToolResult] = []
        state_stored_by_tool = False
        total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        content_accumulator = ""

        # UGO: action history and tool-call dedup hashes (generator path)
        ugo_action_history_gen: list[str] = []
        ugo_tool_hashes_gen: set[str] = set()

        async def _generate_fn(turn: int):
            nonlocal messages
            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            gen_kwargs: dict[str, Any] = {}
            if openai_tools:
                gen_kwargs["tools"] = openai_tools

            result = self._generate(messages, **gen_kwargs)
            usage = result.get("usage", {})
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

            content = result.get("content", "")
            raw_tool_calls = result.get("tool_calls", [])

            if not raw_tool_calls:
                content = self._check_continuation(result, messages)
                return {"content": content, "tool_calls": [], "usage": usage}

            return {"content": content, "tool_calls": raw_tool_calls, "usage": usage}

        async def _tool_executor_fn(tool_calls_raw: list[dict]):
            nonlocal state_stored_by_tool, content_accumulator

            # UGO: evaluate utility before executing tool calls (generator path)
            if self._is_ugo_enabled():
                confidence = self._budget.remaining_pct / 100.0
                ugo_scores = score_actions(
                    current_confidence=confidence,
                    budget=self._budget,
                    action_history=ugo_action_history_gen,
                    tool_call_hashes=ugo_tool_hashes_gen,
                )
                top_action = ugo_scores[0].action if ugo_scores else "tool_call"
                logger.debug(
                    "UGO(gen): top_action=%s utility=%.3f (confidence=%.2f)",
                    top_action,
                    ugo_scores[0].utility if ugo_scores else 0.0,
                    confidence,
                )
                ugo_action_history_gen.append(top_action if top_action != "tool_call" else "tool_call")

            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", "{}"),
                )
                for i, tc in enumerate(tool_calls_raw)
            ]

            # Append assistant message with tool calls
            messages.append(Message(
                role=Role.ASSISTANT,
                content=content_accumulator,
                tool_calls=tool_calls,
            ))

            for tc in tool_calls:
                # Loop guard check
                if self._loop_guard:
                    verdict = self._loop_guard.check_call(tc.name, tc.arguments)
                    if verdict.blocked:
                        tool_result = ToolResult(
                            tool_name=tc.name,
                            content=f"Loop guard: {verdict.reason}",
                            success=False,
                        )
                        all_tool_results.append(tool_result)
                        messages.append(Message(
                            role=Role.TOOL,
                            content=tool_result.content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))
                        continue

                tool_result = self._executor.execute(tc)
                all_tool_results.append(tool_result)

                # Phase 31: Record tool use in BATS budget tracker
                self._budget.record_tool_use(tc.name)

                # UGO: track tool call hash for redundancy detection (generator path)
                ugo_tool_hashes_gen.add(f"{tc.name}:{tc.arguments}")

                # Track if agent stored state via memory_store
                if tc.name == "memory_store" and self._operator_id:
                    try:
                        args = json.loads(tc.arguments)
                        state_key = f"operator:{self._operator_id}:state"
                        if args.get("key", "") == state_key:
                            state_stored_by_tool = True
                    except (json.JSONDecodeError, TypeError):
                        pass

                messages.append(Message(
                    role=Role.TOOL,
                    content=tool_result.content,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

                # Phase 31: Inject compact budget status after tool result
                if self._is_bats_enabled():
                    messages.append(Message(
                        role=Role.SYSTEM,
                        content=self._budget.format_status(),
                    ))

        gen = agent_loop(
            generate_fn=_generate_fn,
            tool_executor_fn=_tool_executor_fn,
            max_turns=self._max_turns,
            abort_signal=kwargs.get("abort_signal"),
        )

        final_content = ""
        async for tick in gen:
            if tick.content:
                content_accumulator = tick.content
                final_content = tick.content
            tick.tokens_used = sum(total_usage.values())
            yield tick

        # 6. Session lifecycle: save regardless of how loop terminated
        self._save_session(input, final_content)

        # 7. Auto-persist state if agent didn't do it explicitly
        if not state_stored_by_tool:
            self._auto_persist_state(final_content)

        self._emit_turn_end(turns=len(all_tool_results) + 1, content_length=len(final_content))

    # ------------------------------------------------------------------
    # Task 18b-03: Stable-prefix prompt caching
    # ------------------------------------------------------------------

    @staticmethod
    def _is_prompt_cache_enabled() -> bool:
        return os.environ.get("ANATOMY_PROMPT_CACHE", "").lower() in ("true", "1")

    @staticmethod
    def _is_prompt_builder_enabled() -> bool:
        return os.environ.get("ANATOMY_PROMPT_BUILDER", "").lower() in ("true", "1")

    def invalidate_prompt_cache(self) -> None:
        """Clear the cached stable prefix so it is re-rendered on the next tick.

        Call when:
        - System prompt is updated via configuration change
        - DNA profile changes (circuit breaker state transition)
        - Lifecycle document is hot-reloaded

        Phase 18b-03 Task 2.
        """
        self._cached_stable_prefix = None
        logger.debug(
            "Operative prompt cache invalidated for %s",
            self._operator_id or "unknown",
        )

    def _build_cached_system_prompt(self) -> str | None:
        """Build the full system prompt, caching the stable prefix across ticks.

        When ANATOMY_PROMPT_BUILDER is enabled, uses PromptBuilder to assemble
        the system prompt with scope-aware ordering (STATIC -> SESSION -> TURN)
        and cache-control annotations for Phase 18b integration.

        When ANATOMY_PROMPT_CACHE is enabled:
        - The stable portion (daemon identity + tool defs + safety rules,
          i.e. ``self._system_prompt``) is rendered ONCE and stored in
          ``self._cached_stable_prefix``.
        - Each tick only re-renders the volatile suffix (previous state).
        - The final prompt is ``stable_prefix + volatile_suffix``.

        This maximises prompt-cache hits because the LLM provider sees
        an identical prefix on every request.

        When both flags are OFF, falls back to the original behaviour:
        assemble the full prompt from scratch every tick.
        """
        # Phase 18a: PromptBuilder integration
        if self._is_prompt_builder_enabled():
            return self._build_with_prompt_builder()

        if self._is_prompt_cache_enabled():
            # Render stable prefix once
            if self._cached_stable_prefix is None and self._system_prompt:
                self._cached_stable_prefix = self._system_prompt
                logger.debug(
                    "Operative prompt cache: cached %d-char stable prefix for %s",
                    len(self._cached_stable_prefix),
                    self._operator_id or "unknown",
                )

            # Build volatile suffix (changes every tick).
            # The CACHE_BOUNDARY_MARKER is placed between the stable prefix
            # and the volatile suffix so that _build_system_blocks() in
            # shared/llm_client.py can split the prompt for Anthropic's
            # cache_control annotation.
            from shared.prompt_builder import CACHE_BOUNDARY_MARKER

            previous_state = self._recall_state()

            # Assemble: cached prefix + boundary + volatile suffix
            if self._cached_stable_prefix and previous_state:
                return (
                    f"{self._cached_stable_prefix}"
                    f"{CACHE_BOUNDARY_MARKER}"
                    f"\n## Previous State\n{previous_state}"
                )
            if self._cached_stable_prefix:
                return self._cached_stable_prefix
            return None

        # Flag OFF: original behaviour
        sys_parts: list[str] = []
        if self._system_prompt:
            sys_parts.append(self._system_prompt)

        previous_state = self._recall_state()
        if previous_state:
            sys_parts.append(f"\n## Previous State\n{previous_state}")

        return "\n\n".join(sys_parts) if sys_parts else None

    def _build_with_prompt_builder(self) -> str | None:
        """Assemble the system prompt using PromptBuilder (Phase 18a).

        Sections are ordered by scope: STATIC (daemon identity) ->
        SESSION (state) -> TURN (current task).  This ordering maximises
        cache-prefix reuse in Phase 18b.
        """
        try:
            from shared.prompt_builder import PromptBuilder, PromptScope
        except ImportError:
            logger.debug("PromptBuilder not available, falling back to string concat")
            return self._build_cached_system_prompt_fallback()

        pb = PromptBuilder()

        # STATIC: daemon identity and system prompt (stable across ticks)
        if self._system_prompt:
            pb.add_section("identity", self._system_prompt, PromptScope.STATIC, priority=10)

        # SESSION: previous state (changes per session but stable within a tick)
        previous_state = self._recall_state()
        if previous_state:
            pb.add_section(
                "previous_state",
                f"## Previous State\n{previous_state}",
                PromptScope.SESSION,
                priority=5,
            )

        result = pb.build_system()
        return result if result else None

    def _build_cached_system_prompt_fallback(self) -> str | None:
        """Fallback when PromptBuilder is unavailable."""
        sys_parts: list[str] = []
        if self._system_prompt:
            sys_parts.append(self._system_prompt)
        previous_state = self._recall_state()
        if previous_state:
            sys_parts.append(f"\n## Previous State\n{previous_state}")
        return "\n\n".join(sys_parts) if sys_parts else None

    def _build_operative_messages(
        self,
        input: str,
        context: AgentContext | None,
        *,
        system_prompt: str | None = None,
        session_messages: list[Message] | None = None,
    ) -> list[Message]:
        """Build message list with system prompt, session history, and input."""
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message(role=Role.SYSTEM, content=system_prompt))
        # Inject session history (recent messages from previous ticks)
        if session_messages:
            messages.extend(session_messages)
        # Context conversation (e.g. memory injection)
        if context and context.conversation.messages:
            messages.extend(context.conversation.messages)
        messages.append(Message(role=Role.USER, content=input))
        return messages

    def _recall_state(self) -> str:
        """Retrieve previous operator state from memory backend."""
        if not self._memory_backend or not self._operator_id:
            return ""
        state_key = f"operator:{self._operator_id}:state"
        try:
            result = self._memory_backend.retrieve(state_key)
            if result:
                return result if isinstance(result, str) else str(result)
        except (OSError, RuntimeError, ValueError, KeyError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("No previous state for operator %s", self._operator_id)
        return ""

    def _load_session(self) -> list[Message]:
        """Load recent session history for this operator."""
        if not self._session_store or not self._operator_id:
            return []
        session_id = f"operator:{self._operator_id}"
        try:
            session = self._session_store.get_or_create(session_id)
            if hasattr(session, "messages") and session.messages:
                # Return last 10 messages to avoid context overflow
                recent = session.messages[-10:]
                return [
                    Message(
                        role=Role(m.get("role", "user")),
                        content=m.get("content", ""),
                    )
                    for m in recent
                    if isinstance(m, dict)
                ]
        except (OSError, RuntimeError, ValueError, KeyError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("Could not load session for operator %s", self._operator_id)
        return []

    def _save_session(self, input_text: str, response: str) -> None:
        """Save the tick's prompt and response to the session store."""
        if not self._session_store or not self._operator_id:
            return
        session_id = f"operator:{self._operator_id}"
        try:
            self._session_store.save_message(
                session_id, {"role": "user", "content": input_text},
            )
            self._session_store.save_message(
                session_id, {"role": "assistant", "content": response},
            )
        except (OSError, RuntimeError, ValueError, KeyError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("Could not save session for operator %s", self._operator_id)

    def _auto_persist_state(self, content: str) -> None:
        """Auto-persist a state summary if the agent didn't store state explicitly."""
        if not self._memory_backend or not self._operator_id:
            return
        state_key = f"operator:{self._operator_id}:state"
        try:
            # Store a summary of the agent's response as state
            summary = content[:1000] if content else ""
            self._memory_backend.store(state_key, summary)
        except (OSError, RuntimeError, ValueError, KeyError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug(
                "Could not auto-persist state for operator %s",
                self._operator_id,
            )


__all__ = ["OperativeAgent"]
