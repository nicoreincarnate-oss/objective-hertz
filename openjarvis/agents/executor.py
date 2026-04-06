"""AgentExecutor — runs a single agent tick."""

from __future__ import annotations

import glob
import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING, Any

from openjarvis.agents._stubs import AgentResult
from openjarvis.agents.errors import (
    AgentTickError,
    EscalateError,
    FatalError,
    classify_error,
    retry_delay,
)
from openjarvis.core.events import EventBus, EventType

if TYPE_CHECKING:
    from openjarvis.agents.manager import AgentManager

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


def _normalize_tool_names(raw: Any) -> list[str]:
    """Normalize tool config into a clean list of tool names."""
    if isinstance(raw, str):
        return [name.strip() for name in raw.split(",") if name.strip()]
    if isinstance(raw, list):
        return [name.strip() for name in raw if isinstance(name, str) and name.strip()]
    return []


class AgentExecutor:
    """Executes a single tick for a managed agent.

    Constructor receives a JarvisSystem reference for access to engine,
    tools, config, memory backends, and all other primitives.
    """

    def __init__(
        self,
        manager: AgentManager,
        event_bus: EventBus,
        system: Any = None,
        trace_store: Any = None,
    ) -> None:
        self._system = system
        self._manager = manager
        self._bus = event_bus
        self._trace_store = trace_store

    def set_system(self, system: Any) -> None:
        """Deferred system injection — called after JarvisSystem is constructed."""
        self._system = system

    def run_ephemeral(
        self,
        agent_type: str,
        system_prompt: str,
        input_text: str,
        tools: list[str] | None = None,
    ) -> Any:
        """Run a one-shot agent turn with no lifecycle tracking."""
        from openjarvis.core.registry import AgentRegistry

        agent_cls = AgentRegistry.get(agent_type)
        agent = agent_cls(
            engine=getattr(self._manager, '_engine', None),
            system_prompt=system_prompt,
            bus=self._bus,
        )
        return agent.run(input_text)

    def _invoke_one_shot(self, agent: dict, config: dict) -> AgentResult:
        """Fast path for single-turn classification agents.

        Skips: retry/continuation logic, agentId/sendMessage prompt injection,
        full trace recording, memory retrieval, multi-turn context assembly.

        Builds a minimal system prompt + user message, makes ONE LLM call,
        and returns the text result.

        Trigger: agent config has ``"one_shot": true`` and the
        ``ANATOMY_MODEL_TIERING`` feature flag is enabled.
        """
        tick_start = time.time()

        engine = self._system.engine if self._system else None
        if engine is None:
            raise FatalError("No engine available for one-shot agent")

        model = config.get("model") or (
            self._system.model if self._system else ""
        )
        if not model:
            raise FatalError("No model configured for one-shot agent")

        # Apply model tiering if available (Phase 18a-03)
        try:
            from shared.model_tiers import get_tiered_model

            model = get_tiered_model(agent.get("id", ""), model)
        except ImportError:
            pass

        system_prompt = config.get("system_prompt", "")
        instruction = config.get("instruction", "")
        input_text = instruction or agent.get("summary_memory", "") or "Classify."

        # Append any pending messages
        pending = self._manager.get_pending_messages(agent["id"])
        if pending:
            user_msgs = "\n".join(f"User: {m['content']}" for m in pending)
            input_text = f"{input_text}\n\nNew instructions:\n{user_msgs}"
            for m in pending:
                self._manager.mark_message_delivered(m["id"])

        logger.info(
            "One-shot agent %s: model=%s, input_len=%d",
            agent.get("id", "?"), model, len(input_text),
        )

        # Single LLM call — no retry, no continuation, no tool injection
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": input_text})

        result = engine.generate(
            messages,
            model=model,
            max_tokens=config.get("max_tokens", 256),
            temperature=config.get("temperature", 0.0),
        )

        content = result.get("content", "")
        tick_duration = time.time() - tick_start

        logger.info(
            "One-shot agent %s completed in %.2fs",
            agent.get("id", "?"), tick_duration,
        )

        return AgentResult(
            content=content,
            tool_results=[],
            turns=1,
            metadata={
                "one_shot": True,
                "duration": tick_duration,
                **result.get("usage", {}),
            },
        )

    def _drain_pending_messages(self, agent_id: str) -> list[dict]:
        """Drain buffered A2A messages before tool execution begins.

        When ANATOMY_COORDINATION_V2 is enabled, messages from other agents
        are buffered in an AgentMessageQueue during tool execution. This
        method drains them at the start of each tick so they are processed
        between tool rounds, not mid-execution.

        Returns the list of drained messages (empty if flag is off or no
        queue is available).
        """
        if os.environ.get("ANATOMY_COORDINATION_V2", "").lower() not in ("true", "1"):
            return []

        try:
            from shared.message_queue import AgentMessageQueue

            queue: AgentMessageQueue | None = getattr(self, "_message_queues", {}).get(agent_id)
            if queue is None:
                return []

            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # We are inside an async context -- schedule and return
                # This should not normally happen in execute_tick (sync),
                # but handle defensively.
                return []

            # Run drain synchronously since execute_tick is sync
            messages = asyncio.get_event_loop().run_until_complete(queue.drain())
            if messages:
                logger.debug(
                    "Drained %d pending messages for agent %s",
                    len(messages),
                    agent_id,
                )
            return messages
        except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug(
                "Message drain failed for agent %s: %s",
                agent_id,
                exc,
                exc_info=True,
            )
            return []

    def register_message_queue(self, agent_id: str, queue: "Any") -> None:
        """Register an AgentMessageQueue for a managed agent."""
        if not hasattr(self, "_message_queues"):
            self._message_queues: dict[str, Any] = {}
        self._message_queues[agent_id] = queue

    def execute_tick(self, agent_id: str) -> None:
        """Run one tick for the given agent.

        1. Drain pending messages (ANATOMY_COORDINATION_V2)
        2. Acquire concurrency guard (start_tick)
        3. Invoke agent with retry logic
        4. Update stats
        5. Release guard (end_tick)
        """
        # Phase 22-04: drain buffered messages BEFORE tool execution
        drained = self._drain_pending_messages(agent_id)

        try:
            self._manager.start_tick(agent_id)
        except ValueError:
            logger.warning("Agent %s already running, skipping tick", agent_id)
            return

        agent = self._manager.get_agent(agent_id)
        if agent is None:
            logger.error("Agent %s not found", agent_id)
            return

        # If we drained messages, inject them as pending messages
        if drained:
            for msg in drained:
                content = msg.get("text", msg.get("content", ""))
                if content:
                    self._manager.send_message(agent_id, content)

        self._bus.publish(EventType.AGENT_TICK_START, {
            "agent_id": agent_id,
            "agent_name": agent["name"],
        })

        # Activity tracking: subscribe to tool/inference events
        def _on_activity(event: Any) -> None:
            if event.data.get("agent") == agent_id:
                self._manager.update_agent(agent_id, last_activity_at=time.time())

        self._bus.subscribe(EventType.TOOL_CALL_START, _on_activity)
        self._bus.subscribe(EventType.INFERENCE_START, _on_activity)

        # Trace recording: collect tool call steps
        trace_steps: list[dict[str, Any]] = []

        def _on_tool_start(event: Any) -> None:
            if event.data.get("agent") == agent_id:
                trace_steps.append({
                    "type": "tool_call",
                    "input": {
                        "tool": event.data.get("tool"),
                        "args": event.data.get("args"),
                    },
                    "start_time": event.timestamp,
                })

        def _on_tool_end(event: Any) -> None:
            if event.data.get("agent") == agent_id and trace_steps:
                for step in reversed(trace_steps):
                    if step["type"] == "tool_call" and "output" not in step:
                        step["output"] = {
                            "result": str(event.data.get("result", ""))[:4096],
                        }
                        step["duration"] = event.data.get("duration", 0)
                        break

        if self._trace_store:
            self._bus.subscribe(EventType.TOOL_CALL_START, _on_tool_start)
            self._bus.subscribe(EventType.TOOL_CALL_END, _on_tool_end)

        tick_start = time.time()
        result = None
        error_info = None

        try:
            result = self._run_with_retries(agent)
        except AgentTickError as e:
            error_info = e
        finally:
            self._bus.unsubscribe(EventType.TOOL_CALL_START, _on_activity)
            self._bus.unsubscribe(EventType.INFERENCE_START, _on_activity)

            if self._trace_store:
                self._bus.unsubscribe(EventType.TOOL_CALL_START, _on_tool_start)
                self._bus.unsubscribe(EventType.TOOL_CALL_END, _on_tool_end)

            tick_duration = time.time() - tick_start
            self._finalize_tick(agent_id, result, error_info, tick_duration)

            if self._trace_store:
                self._save_trace(
                    agent_id, agent, result, error_info,
                    tick_start, tick_duration, trace_steps,
                )

            # Phase 22-06: comprehensive agent cleanup after tick
            self._cleanup_agent_tick(agent_id)

    def _cleanup_agent_tick(self, agent_id: str) -> None:
        """Comprehensive post-tick cleanup (Phase 22-06 — D-03).

        Runs after every tick (success or failure) to prevent resource leaks:
        - Unsubscribes any lingering event handlers registered during the tick
        - Clears temporary file caches scoped to the agent
        - Logs cleanup at DEBUG level

        Gated behind ANATOMY_COORDINATION_V2 feature flag.
        """
        if os.environ.get("ANATOMY_COORDINATION_V2", "").lower() not in ("true", "1"):
            return

        cleaned_events = 0
        cleaned_files = 0

        # 1. Unsubscribe any event handlers that reference this agent_id.
        #    The EventBus stores subscribers per event type. We scan all
        #    registered handlers and remove any whose closure captures this
        #    agent_id (detected via __qualname__ containing 'execute_tick').
        try:
            for event_type in list(EventType):
                subs = self._bus._subscribers.get(event_type, [])
                to_remove = []
                for handler in subs:
                    # Check if handler is a local closure from execute_tick
                    # that was not already removed (belt-and-suspenders)
                    qualname = getattr(handler, "__qualname__", "")
                    if "execute_tick" in qualname:
                        to_remove.append(handler)
                for handler in to_remove:
                    try:
                        self._bus.unsubscribe(event_type, handler)
                        cleaned_events += 1
                    except (ValueError, KeyError):
                        pass  # Already removed
        except (RuntimeError, ValueError, TypeError, AttributeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug(
                "Agent %s cleanup: event unsubscribe scan failed: %s",
                agent_id,
                exc,
                exc_info=True,
            )

        # 2. Clear temporary file caches scoped to this agent
        #    Agents may write temp files via shell_exec or file operations.
        #    Pattern: /tmp/oj_agent_<agent_id>_*
        try:
            pattern = os.path.join(
                tempfile.gettempdir(), f"oj_agent_{agent_id}_*"
            )
            for tmp_path in glob.glob(pattern):
                try:
                    os.remove(tmp_path)
                    cleaned_files += 1
                except OSError:
                    pass  # File already gone or locked
        except (OSError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug(
                "Agent %s cleanup: temp file removal failed: %s",
                agent_id,
                exc,
                exc_info=True,
            )

        logger.debug(
            "Agent %s tick cleanup: unsubscribed %d event handlers, "
            "removed %d temp files",
            agent_id,
            cleaned_events,
            cleaned_files,
        )

    def _run_with_retries(self, agent: dict) -> AgentResult:
        """Invoke the agent, retrying on RetryableError up to _MAX_RETRIES.

        When ANATOMY_UNIFIED_LLM is enabled, errors are withheld during
        recovery attempts instead of being immediately propagated.  Only
        after all recovery is exhausted are the errors surfaced (logged
        and raised), preventing cascading failures in downstream systems
        (EventBus listeners, Titan pipeline, A2A consumers).
        """
        use_withholding = os.environ.get(
            "ANATOMY_UNIFIED_LLM", ""
        ).lower() in ("true", "1")

        withholder = None
        if use_withholding:
            try:
                from shared.error_withholding import ErrorWithholder

                withholder = ErrorWithholder(max_recovery_attempts=_MAX_RETRIES)
            except ImportError:
                logger.debug(
                    "shared.error_withholding not available, "
                    "falling back to standard retry"
                )

        last_error: AgentTickError | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                result = self._invoke_agent(agent)
                # Recovery succeeded — discard any withheld errors
                if withholder is not None:
                    withholder.clear()
                return result
            except AgentTickError as e:
                if not e.retryable or attempt == _MAX_RETRIES - 1:
                    if withholder is not None:
                        withholder.withhold(e)
                        for held in withholder.release():
                            logger.warning(
                                "Agent %s withheld error released: %s",
                                agent["id"],
                                held,
                            )
                    raise
                last_error = e
                if withholder is not None:
                    withholder.withhold(e)
                delay = retry_delay(attempt)
                logger.info(
                    "Agent %s tick retry %d/%d in %ds: %s",
                    agent["id"], attempt + 1, _MAX_RETRIES, delay, e,
                )
                time.sleep(delay)
            except Exception as e:
                classified = classify_error(e)
                if not classified.retryable or attempt == _MAX_RETRIES - 1:
                    if withholder is not None:
                        withholder.withhold(classified)
                        for held in withholder.release():
                            logger.warning(
                                "Agent %s withheld error released: %s",
                                agent["id"],
                                held,
                            )
                    raise classified from e
                if withholder is not None:
                    withholder.withhold(classified)
                delay = retry_delay(attempt)
                logger.info(
                    "Agent %s tick retry %d/%d in %ds: %s",
                    agent["id"], attempt + 1, _MAX_RETRIES, delay, e,
                )
                time.sleep(delay)

        # Should not reach here, but just in case
        raise last_error or FatalError("max retries exhausted")

    def _invoke_agent(self, agent: dict) -> AgentResult:
        """Invoke the actual agent run. Tests mock this method."""
        from openjarvis.agents import AgentRegistry

        agent_type = agent.get("agent_type", "monitor_operative")
        agent_cls = AgentRegistry.get(agent_type)
        if agent_cls is None:
            raise FatalError(f"Unknown agent type: {agent_type}")

        config = agent.get("config", {})

        # One-shot optimization (Phase 18a-04 — D-05)
        # Agents with "one_shot": true in their config are simple classifiers
        # that do one LLM call and return. Skip all multi-turn overhead.
        # Gated behind ANATOMY_MODEL_TIERING feature flag.
        if (
            config.get("one_shot") is True
            and os.environ.get("ANATOMY_MODEL_TIERING", "").lower()
            in ("true", "1")
        ):
            return self._invoke_one_shot(agent, config)

        # Resolve engine + model from JarvisSystem
        engine = self._system.engine if self._system else None
        if engine is None:
            raise FatalError("No engine available in JarvisSystem")

        # Phase 23: When unified LLM is active, wrap engine with fallback chain
        if os.environ.get("ANATOMY_UNIFIED_LLM", "").lower() in ("true", "1"):
            try:
                from shared.llm_factory import UnifiedEngineAdapter
                engine = UnifiedEngineAdapter(engine)
            except ImportError:
                pass  # Graceful degradation if factory module unavailable

        model = config.get("model") or (
            self._system.model
            if self._system else ""
        )
        if not model:
            raise FatalError("No model configured for agent")

        # Optionally override model via router policy
        router_policy_key = config.get("router_policy")
        if router_policy_key and self._system:
            try:
                from openjarvis.core.registry import RouterPolicyRegistry
                from openjarvis.learning.routing.types import (
                    build_routing_context,
                )

                available_models = config.get("available_models") or []
                if not available_models:
                    try:
                        available_models = list(self._system.engine.list_models())
                    except Exception:
                        available_models = []
                if not available_models:
                    available_models = [model]

                policy = RouterPolicyRegistry.create(
                    router_policy_key,
                    available_models=available_models,
                )
                instruction = config.get("instruction", "")
                ctx = build_routing_context(instruction)
                selected = policy.select_model(ctx)
                if selected:
                    model = selected
            except (ImportError, KeyError, ValueError, TypeError, RuntimeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                pass  # Fall back to configured model

        # Build runtime tool set from agent config. Without this, managed
        # agents are effectively tool-blind even though the framework
        # exposes a large registered tool surface.
        tool_names = _normalize_tool_names(config.get("tools"))
        agent_tools = []
        if tool_names and self._system:
            try:
                agent_tools = self._system._build_tools(tool_names)
            except (ImportError, KeyError, ValueError, TypeError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.warning(
                    "Failed to build tools for agent %s: %s — %s",
                    agent.get("id", ""),
                    tool_names,
                    exc,
                    exc_info=True,
                )

        # Phase 21 — Per-agent permission filtering (gated)
        permission_profile = None
        if os.environ.get("ANATOMY_AGENT_PERMISSIONS", "").lower() in ("true", "1"):
            try:
                from shared.permissions import (
                    get_permission_profile,
                    is_tool_allowed,
                    publish_escalation,
                )

                permission_profile = get_permission_profile(config)
                if tool_names:
                    filtered_names = []
                    for tname in tool_names:
                        if is_tool_allowed(permission_profile, tname):
                            filtered_names.append(tname)
                        else:
                            publish_escalation(
                                self._bus,
                                agent.get("id", ""),
                                tname,
                                f"tool={tname} requested by agent config",
                                f"Denied by permission profile: level={permission_profile.level.value}",
                            )
                    if filtered_names != tool_names:
                        logger.info(
                            "Agent %s: permission filter reduced tools from %s to %s",
                            agent.get("id", ""),
                            tool_names,
                            filtered_names,
                        )
                        # Rebuild tools with the filtered set
                        if self._system and filtered_names:
                            try:
                                agent_tools = self._system._build_tools(filtered_names)
                            except (ImportError, KeyError, ValueError, TypeError, RuntimeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                                logger.warning(
                                    "Failed to rebuild filtered tools for agent %s",
                                    agent.get("id", ""),
                                    exc_info=True,
                                )
                        elif not filtered_names:
                            agent_tools = []
            except ImportError:
                logger.debug("shared.permissions not available, skipping permission filtering")

        # Context stripping for lightweight agents (gated by ANATOMY_MODEL_TIERING)
        system_prompt = config.get("system_prompt")
        if config.get("context_level") == "minimal" and system_prompt:
            try:
                from shared.context_stripper import is_enabled, strip_context

                if is_enabled():
                    system_prompt = strip_context(system_prompt)
            except (ImportError, ValueError, TypeError, RuntimeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                pass  # Fall back to full context if stripping fails

        agent_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "bus": self._bus,
            "max_turns": config.get("max_turns", 10),
            "temperature": config.get(
                "temperature",
                getattr(self._system, "config", None).intelligence.temperature
                if self._system and getattr(self._system, "config", None)
                else 0.7,
            ),
            "max_tokens": config.get(
                "max_tokens",
                getattr(self._system, "config", None).intelligence.max_tokens
                if self._system and getattr(self._system, "config", None)
                else 1024,
            ),
        }
        if getattr(agent_cls, "accepts_tools", False):
            agent_kwargs["tools"] = agent_tools
        if self._system and getattr(self._system, "capability_policy", None) is not None:
            agent_kwargs["capability_policy"] = self._system.capability_policy
        if config.get("operator_id"):
            agent_kwargs["operator_id"] = config["operator_id"]
        if self._system:
            if getattr(self._system, "session_store", None) is not None:
                agent_kwargs["session_store"] = self._system.session_store
            if getattr(self._system, "memory_backend", None) is not None:
                agent_kwargs["memory_backend"] = self._system.memory_backend

        # Construct agent instance (BaseAgent requires engine, model as positional args)
        constructor_variants = [
            dict(agent_kwargs),
            {
                k: v for k, v in agent_kwargs.items()
                if k != "system_prompt"
            },
            {
                k: v for k, v in agent_kwargs.items()
                if k not in (
                    "system_prompt",
                    "capability_policy",
                    "session_store",
                    "memory_backend",
                    "operator_id",
                )
            },
            {
                k: v for k, v in agent_kwargs.items()
                if k not in (
                    "tools",
                    "system_prompt",
                    "capability_policy",
                    "session_store",
                    "memory_backend",
                    "operator_id",
                )
            },
        ]
        last_error: TypeError | None = None
        for kwargs_variant in constructor_variants:
            try:
                agent_instance = agent_cls(engine, model, **kwargs_variant)
                break
            except TypeError as exc:
                last_error = exc
        else:
            raise last_error or TypeError("Could not construct agent instance")

        # Build input from instruction + summary_memory + pending messages
        instruction = config.get("instruction", "")
        memory = agent.get("summary_memory", "")
        if instruction:
            input_text = f"Standing instruction: {instruction}"
            if memory:
                input_text += f"\n\nPrevious context: {memory}"
        else:
            input_text = memory or "Continue your assigned task."
        pending = self._manager.get_pending_messages(agent["id"])
        if pending:
            user_msgs = "\n".join(f"User: {m['content']}" for m in pending)
            input_text = f"{input_text}\n\nNew instructions:\n{user_msgs}"
            for m in pending:
                self._manager.mark_message_delivered(m["id"])

        # Phase 26b-04: Route to run_iter() when generator loop is enabled
        # and the agent has implemented it (has_run_iter() returns True).
        if (
            os.environ.get("ANATOMY_GENERATOR_LOOP", "").lower() in ("true", "1")
            and hasattr(agent_instance, "has_run_iter")
            and agent_instance.has_run_iter()
        ):
            logger.info(
                "Agent %s: using generator-based run_iter()",
                agent.get("id", "?"),
            )

        # Build AgentContext with memory results from FTS5 backend
        from openjarvis.agents._stubs import AgentContext

        agent_ctx = AgentContext()
        memory_results = []

        if (
            self._system
            and getattr(self._system, "memory_backend", None)
            and getattr(self._system, "config", None)
            and self._system.config.agent.context_from_memory
        ):
            try:
                from openjarvis.tools.storage.context import (
                    ContextConfig,
                    format_context,
                )

                sys_cfg = self._system.config
                ctx_cfg = ContextConfig(
                    top_k=sys_cfg.memory.context_top_k,
                    min_score=sys_cfg.memory.context_min_score,
                    max_context_tokens=sys_cfg.memory.context_max_tokens,
                )
                # Use pending user messages as query, fall back to instruction
                query = ""
                if pending:
                    query = " ".join(m["content"] for m in pending)
                elif instruction:
                    query = instruction

                if query:
                    results = self._system.memory_backend.retrieve(
                        query, top_k=ctx_cfg.top_k,
                    )
                    memory_results = [
                        r for r in results if r.score >= ctx_cfg.min_score
                    ]
                    if memory_results:
                        # Prepend retrieved context to input for agents
                        # that don't inspect AgentContext.memory_results
                        retrieved = format_context(memory_results)
                        input_text = (
                            f"Retrieved context from knowledge base:\n"
                            f"{retrieved}\n\n{input_text}"
                        )
            except (ImportError, ValueError, TypeError, KeyError, RuntimeError, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
                pass  # Don't break agent tick if memory retrieval fails

        agent_ctx.memory_results = memory_results
        return agent_instance.run(input_text, context=agent_ctx)

    def _build_error_detail(self, error: AgentTickError) -> dict[str, Any]:
        """Build structured error detail for trace metadata."""
        import traceback

        from openjarvis.agents.errors import (
            EscalateError,
            FatalError,
            suggest_action,
        )

        if isinstance(error, EscalateError):
            error_type = "escalate"
        elif isinstance(error, FatalError):
            error_type = "fatal"
        else:
            error_type = "retryable"

        return {
            "error_type": error_type,
            "error_message": str(error)[:2000],
            "suggested_action": suggest_action(error),
            "stack_trace_summary": "".join(
                traceback.format_exception(type(error), error, error.__traceback__)[-3:]
            )[:1000] if error.__traceback__ else "",
        }

    def _finalize_tick(
        self,
        agent_id: str,
        result: AgentResult | None,
        error: AgentTickError | None,
        duration: float,
    ) -> None:
        """Update agent state after tick completion or failure."""
        if error is None:
            # Success
            self._manager.end_tick(agent_id)
            self._manager.update_agent(agent_id, total_runs_increment=1)

            # Accumulate budget metrics from AgentResult metadata
            if result:
                tokens = result.metadata.get("tokens_used", 0)
                cost = result.metadata.get("cost", 0.0)
                budget_kwargs: dict[str, Any] = {"stall_retries": 0}
                if tokens > 0:
                    budget_kwargs["total_tokens_increment"] = tokens
                if cost > 0:
                    budget_kwargs["total_cost_increment"] = cost
                self._manager.update_agent(agent_id, **budget_kwargs)

                self._manager.update_summary_memory(
                    agent_id, result.content[:2000],
                )
                self._manager.store_agent_response(agent_id, result.content[:2000])

            # Budget enforcement (post-tick check)
            agent_data = self._manager.get_agent(agent_id)
            if agent_data:
                config = agent_data.get("config", {})
                max_cost = config.get("max_cost", 0)
                max_tokens = config.get("max_tokens", 0)
                exceeded = False
                if max_cost > 0 and agent_data["total_cost"] > max_cost:
                    exceeded = True
                if max_tokens > 0 and agent_data["total_tokens"] > max_tokens:
                    exceeded = True
                if exceeded:
                    self._manager.update_agent(agent_id, status="budget_exceeded")
                    self._bus.publish(EventType.AGENT_BUDGET_EXCEEDED, {
                        "agent_id": agent_id,
                        "total_cost": agent_data["total_cost"],
                        "total_tokens": agent_data["total_tokens"],
                        "max_cost": max_cost,
                        "max_tokens": max_tokens,
                    })
            self._bus.publish(EventType.AGENT_TICK_END, {
                "agent_id": agent_id,
                "duration": duration,
                "status": "ok",
            })
        elif isinstance(error, EscalateError):
            self._manager.end_tick(agent_id)
            self._manager.update_agent(agent_id, status="needs_attention")
            self._bus.publish(EventType.AGENT_TICK_ERROR, {
                "agent_id": agent_id,
                "error": str(error),
                "error_type": "escalate",
                "duration": duration,
            })
        else:
            self._manager.end_tick(agent_id)
            self._manager.update_agent(agent_id, status="error")
            # Write error detail to summary_memory so frontend can display it
            error_msg = str(error)[:2000]
            self._manager.update_summary_memory(agent_id, f"ERROR: {error_msg}")
            self._bus.publish(EventType.AGENT_TICK_ERROR, {
                "agent_id": agent_id,
                "error": str(error),
                "error_type": (
                    "fatal" if isinstance(error, FatalError) else "retryable_exhausted"
                ),
                "duration": duration,
            })

    def _save_trace(
        self,
        agent_id: str,
        agent: dict,
        result: AgentResult | None,
        error: AgentTickError | None,
        tick_start: float,
        tick_duration: float,
        trace_steps: list[dict[str, Any]],
    ) -> None:
        """Persist an execution trace to the trace store."""
        from openjarvis.core.types import StepType, Trace, TraceStep

        steps = []
        for s in trace_steps:
            steps.append(TraceStep(
                step_type=(
                    StepType.TOOL_CALL
                    if s["type"] == "tool_call"
                    else StepType.GENERATE
                ),
                input=s.get("input", {}),
                output=s.get("output", {}),
                duration_seconds=s.get("duration", 0),
                timestamp=s.get("start_time", tick_start),
            ))

        metadata: dict[str, Any] = {}
        if error is not None:
            metadata["error_detail"] = self._build_error_detail(error)

        outcome = "success" if error is None else "error"
        trace = Trace(
            agent=agent_id,
            query=agent.get("summary_memory", "")[:200],
            result=result.content[:200] if result else "",
            model=agent.get("config", {}).get("model", ""),
            outcome=outcome,
            steps=steps,
            started_at=tick_start,
            ended_at=tick_start + tick_duration,
            total_latency_seconds=tick_duration,
            metadata=metadata,
        )
        try:
            self._trace_store.save(trace)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.warning(
                "Failed to save trace for agent %s: %s", agent_id, exc, exc_info=True,
            )
