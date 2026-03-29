"""WorkflowEngine — executes a WorkflowGraph against a JarvisSystem.

Supports both synchronous (`run()`) and asynchronous (`run_async()`) execution.
The async path uses ``asyncio.gather()`` for parallel nodes instead of
ThreadPoolExecutor, enabling efficient async I/O throughout the pipeline.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import os
import time
from typing import Any

from openjarvis.core.events import EventBus, EventType
from openjarvis.workflow.graph import WorkflowGraph
from openjarvis.workflow.types import (
    NodeType,
    WorkflowNode,
    WorkflowResult,
    WorkflowStepResult,
)


class WorkflowEngine:
    """Execute DAG-based workflows.

    Sequential nodes run in topological order. Parallel-eligible nodes
    (same execution stage, no inter-dependencies) run via ThreadPoolExecutor.
    Condition nodes evaluate expressions against prior step outputs.
    Loop nodes use LoopGuard from Phase 14.3.
    """

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        max_parallel: int = 4,
        default_node_timeout: int = 300,
    ) -> None:
        self._bus = bus
        self._max_parallel = max_parallel
        self._default_node_timeout = default_node_timeout

    def run(
        self,
        graph: WorkflowGraph,
        system: Any = None,  # JarvisSystem
        *,
        initial_input: str = "",
        context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a workflow graph end-to-end."""
        valid, msg = graph.validate()
        if not valid:
            return WorkflowResult(
                workflow_name=graph.name,
                success=False,
                final_output=f"Invalid workflow: {msg}",
            )

        t0 = time.time()
        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_START,
                {"workflow": graph.name},
            )

        # State: outputs keyed by node_id
        outputs: dict[str, str] = {"_input": initial_input}
        ctx = dict(context or {})
        all_steps: list[WorkflowStepResult] = []
        success = True

        stages = graph.execution_stages()
        for stage in stages:
            if len(stage) == 1:
                # Sequential execution
                step = self._execute_node(
                    graph.get_node(stage[0]),  # type: ignore[arg-type]
                    outputs,
                    ctx,
                    system,
                    graph,
                )
                all_steps.append(step)
                outputs[stage[0]] = step.output
                if not step.success:
                    success = False
                    break
            else:
                # Parallel execution
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(len(stage), self._max_parallel),
                ) as pool:
                    futures = {
                        pool.submit(
                            self._execute_node,
                            graph.get_node(nid),
                            dict(outputs),
                            dict(ctx),
                            system,
                            graph,
                        ): nid
                        for nid in stage
                    }
                    for future in concurrent.futures.as_completed(futures):
                        nid = futures[future]
                        try:
                            step = future.result(
                                timeout=self._default_node_timeout,
                            )
                        except Exception as exc:
                            step = WorkflowStepResult(
                                node_id=nid,
                                success=False,
                                output=f"Node execution error: {exc}",
                            )
                        all_steps.append(step)
                        outputs[nid] = step.output
                        if not step.success:
                            success = False

            if not success:
                break

        total = time.time() - t0
        # Final output is the output of the last executed node
        final_output = all_steps[-1].output if all_steps else ""

        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_END,
                {"workflow": graph.name, "success": success, "duration": total},
            )

        return WorkflowResult(
            workflow_name=graph.name,
            success=success,
            steps=all_steps,
            final_output=final_output,
            total_duration_seconds=total,
        )

    @staticmethod
    def _middleware_enabled() -> bool:
        """Check if the ENABLE_MIDDLEWARE feature flag is active."""
        return os.environ.get("ENABLE_MIDDLEWARE", "").lower() in ("true", "1", "yes")

    def _execute_node_core(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        system: Any,
        graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Core node execution logic (without middleware)."""
        try:
            if node.node_type == NodeType.AGENT:
                result = self._run_agent_node(node, outputs, system, graph)
            elif node.node_type == NodeType.TOOL:
                result = self._run_tool_node(node, outputs, system)
            elif node.node_type == NodeType.CONDITION:
                result = self._run_condition_node(node, outputs)
            elif node.node_type == NodeType.TRANSFORM:
                result = self._run_transform_node(node, outputs)
            elif node.node_type == NodeType.LOOP:
                result = self._run_loop_node(node, outputs, system, graph)
            else:
                result = WorkflowStepResult(
                    node_id=node.id,
                    success=False,
                    output=f"Unknown node type: {node.node_type}",
                )
        except Exception as exc:
            result = WorkflowStepResult(
                node_id=node.id,
                success=False,
                output=f"Node error: {exc}",
            )
        return result

    def _execute_node_with_middleware(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        system: Any,
        graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Wrap node execution with the async middleware chain."""
        try:
            from shared.middleware import build_chain
        except ImportError:
            logger.warning("shared.middleware not available — running without middleware")
            return self._execute_node_core(node, outputs, ctx, system, graph)

        pipeline_name = ctx.get("pipeline", "titan")
        chain = build_chain(pipeline_name)
        stage_ctx: dict[str, Any] = {
            "daemon_name": ctx.get("daemon_name", ""),
            "stage_name": node.id,
            "pipeline": pipeline_name,
            "tools": getattr(node, "tools", None) or [],
        }

        async def _node_handler(c: dict[str, Any]) -> dict[str, Any]:
            step = self._execute_node_core(node, outputs, ctx, system, graph)
            return {
                "success": step.success,
                "output": step.output,
                "node_id": step.node_id,
                "metadata": step.metadata,
            }

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1) as pool:
                    mw_result = pool.submit(
                        lambda: asyncio.run(chain.execute(stage_ctx, _node_handler)),
                    ).result(timeout=self._default_node_timeout)
            else:
                mw_result = asyncio.run(chain.execute(stage_ctx, _node_handler))

            return WorkflowStepResult(
                node_id=node.id,
                success=mw_result.get("success", False),
                output=mw_result.get("output", ""),
                metadata=mw_result.get("metadata", {}),
            )
        except Exception as exc:
            logger.warning("Middleware chain error (falling back to direct): %s", exc)
            return self._execute_node_core(node, outputs, ctx, system, graph)

    def _execute_node(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        system: Any,
        graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Execute a single workflow node, optionally wrapped with middleware."""
        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_NODE_START,
                {"node": node.id, "type": node.node_type.value},
            )

        t0 = time.time()
        if self._middleware_enabled():
            result = self._execute_node_with_middleware(node, outputs, ctx, system, graph)
        else:
            result = self._execute_node_core(node, outputs, ctx, system, graph)

        result.duration_seconds = time.time() - t0

        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_NODE_END,
                {
                    "node": node.id,
                    "success": result.success,
                    "duration": result.duration_seconds,
                },
            )

        return result

    def _get_node_input(
        self, node: WorkflowNode, outputs: dict[str, str], graph: WorkflowGraph,
    ) -> str:
        """Get input for a node from predecessor outputs."""
        preds = graph.predecessors(node.id)
        if preds:
            parts = [outputs.get(p, "") for p in preds if outputs.get(p)]
            return "\n\n".join(parts) if parts else outputs.get("_input", "")
        return outputs.get("_input", "")

    def _run_agent_node(
        self, node: WorkflowNode, outputs: dict[str, str],
        system: Any, graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Execute an agent node."""
        input_text = self._get_node_input(node, outputs, graph)
        if system is None:
            return WorkflowStepResult(
                node_id=node.id,
                success=False,
                output="No system available for agent execution.",
            )
        try:
            result = system.ask(
                input_text,
                agent=node.agent or None,
                tools=node.tools or None,
            )
            return WorkflowStepResult(
                node_id=node.id,
                success=True,
                output=result.get("content", ""),
            )
        except Exception as exc:
            return WorkflowStepResult(
                node_id=node.id,
                success=False,
                output=f"Agent error: {exc}",
            )

    def _run_tool_node(
        self, node: WorkflowNode, outputs: dict[str, str], system: Any,
    ) -> WorkflowStepResult:
        """Execute a tool node."""
        tool_name = node.config.get("tool_name", "")
        tool_args = node.config.get("tool_args", "{}")
        if system and system.tool_executor:
            from openjarvis.core.types import ToolCall
            tc = ToolCall(id=f"wf_{node.id}", name=tool_name, arguments=tool_args)
            tr = system.tool_executor.execute(tc)
            return WorkflowStepResult(
                node_id=node.id,
                success=tr.success,
                output=tr.content,
            )
        return WorkflowStepResult(
            node_id=node.id,
            success=False,
            output="No tool executor available.",
        )

    @staticmethod
    def _safe_eval_condition_fn(expr: str, outputs: dict) -> str:
        """Safely evaluate a simple condition expression."""
        import ast

        # Support simple comparisons: outputs["key"] == "value"
        # Parse the expression as AST and only allow safe operations
        try:
            tree = ast.parse(expr, mode='eval')
            # Walk the AST and only allow safe nodes
            for node in ast.walk(tree):
                if isinstance(node, (ast.Expression, ast.Compare, ast.Subscript,
                                   ast.Constant, ast.Name, ast.Load, ast.Eq,
                                   ast.NotEq, ast.In, ast.NotIn, ast.Str,
                                   ast.Index, ast.BoolOp, ast.And, ast.Or,
                                   ast.UnaryOp, ast.Not)):
                    continue
                # Reject anything not in the whitelist
                raise ValueError(f"Unsafe expression node: {type(node).__name__}")
            # Safe to evaluate with restricted namespace
            return str(eval(expr, {"__builtins__": {}}, {"outputs": outputs}))
        except Exception as e:
            raise ValueError(f"Cannot evaluate condition '{expr}': {e}") from e

    def _run_condition_node(
        self, node: WorkflowNode, outputs: dict[str, str],
    ) -> WorkflowStepResult:
        """Evaluate a condition expression against outputs."""
        expr = node.condition_expr
        if not expr:
            return WorkflowStepResult(
                node_id=node.id, success=True, output="true",
            )
        try:
            result = self._safe_eval_condition_fn(expr, outputs)
        except (ValueError, Exception):
            result = "false"
        return WorkflowStepResult(
            node_id=node.id,
            success=True,
            output=result,
        )

    def _run_transform_node(
        self, node: WorkflowNode, outputs: dict[str, str],
    ) -> WorkflowStepResult:
        """Apply a text transformation."""
        expr = node.transform_expr
        preds = [outputs.get(p, "") for p in outputs if p != "_input"]
        combined = "\n\n".join(preds) if preds else ""
        if expr == "concatenate":
            return WorkflowStepResult(node_id=node.id, output=combined)
        if expr == "first_line":
            return WorkflowStepResult(
                node_id=node.id,
                output=combined.split("\n")[0] if combined else "",
            )
        return WorkflowStepResult(node_id=node.id, output=combined)

    def _run_loop_node(
        self, node: WorkflowNode, outputs: dict[str, str],
        system: Any, graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Execute a loop node (re-runs agent until condition or max iterations)."""
        input_text = self._get_node_input(node, outputs, graph)
        max_iter = node.max_iterations
        last_output = input_text
        iterations_done = 0
        for _ in range(max_iter):
            iterations_done += 1
            if system:
                result = system.ask(last_output, agent=node.agent or None)
                last_output = result.get("content", "")
                # Check if loop should terminate
                if (
                    node.condition_expr
                    and node.condition_expr.lower()
                    in last_output.lower()
                ):
                    break
            else:
                break
        return WorkflowStepResult(
            node_id=node.id,
            success=True,
            output=last_output,
            metadata={"iterations": iterations_done},
        )

    # ------------------------------------------------------------------
    # Async execution path
    # ------------------------------------------------------------------

    async def run_async(
        self,
        graph: WorkflowGraph,
        system: Any = None,
        *,
        initial_input: str = "",
        context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a workflow graph end-to-end using async I/O.

        Sequential nodes are awaited one at a time. Parallel-eligible nodes
        (same execution stage, no inter-dependencies) are dispatched via
        ``asyncio.gather()``.

        The synchronous ``run()`` method is preserved for backward compatibility.
        Prefer ``run_async()`` for new code.
        """
        valid, msg = graph.validate()
        if not valid:
            return WorkflowResult(
                workflow_name=graph.name,
                success=False,
                final_output=f"Invalid workflow: {msg}",
            )

        t0 = time.time()
        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_START,
                {"workflow": graph.name},
            )

        outputs: dict[str, str] = {"_input": initial_input}
        ctx = dict(context or {})
        all_steps: list[WorkflowStepResult] = []
        success = True

        stages = graph.execution_stages()
        for stage in stages:
            if len(stage) == 1:
                step = await self._execute_node_async(
                    graph.get_node(stage[0]),  # type: ignore[arg-type]
                    outputs,
                    ctx,
                    system,
                    graph,
                )
                all_steps.append(step)
                outputs[stage[0]] = step.output
                if not step.success:
                    success = False
                    break
            else:
                tasks = [
                    self._execute_node_async(
                        graph.get_node(nid),
                        dict(outputs),
                        dict(ctx),
                        system,
                        graph,
                    )
                    for nid in stage
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for nid, res in zip(stage, results, strict=True):
                    if isinstance(res, BaseException):
                        step = WorkflowStepResult(
                            node_id=nid,
                            success=False,
                            output=f"Node execution error: {res}",
                        )
                    else:
                        step = res
                    all_steps.append(step)
                    outputs[nid] = step.output
                    if not step.success:
                        success = False

            if not success:
                break

        total = time.time() - t0
        final_output = all_steps[-1].output if all_steps else ""

        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_END,
                {"workflow": graph.name, "success": success, "duration": total},
            )

        return WorkflowResult(
            workflow_name=graph.name,
            success=success,
            steps=all_steps,
            final_output=final_output,
            total_duration_seconds=total,
        )

    def run_sync(
        self,
        graph: WorkflowGraph,
        system: Any = None,
        *,
        initial_input: str = "",
        context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Convenience wrapper: run the async engine from synchronous code.

        Equivalent to ``asyncio.run(self.run_async(...))``.
        """
        return asyncio.run(
            self.run_async(
                graph, system, initial_input=initial_input, context=context,
            )
        )

    async def _execute_node_async(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        system: Any,
        graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Execute a single workflow node (async path)."""
        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_NODE_START,
                {"node": node.id, "type": node.node_type.value},
            )

        t0 = time.time()
        try:
            if node.node_type == NodeType.AGENT:
                result = await self._run_agent_node_async(node, outputs, system, graph)
            elif node.node_type == NodeType.TOOL:
                result = await self._run_tool_node_async(node, outputs, system)
            elif node.node_type == NodeType.CONDITION:
                result = self._run_condition_node(node, outputs)
            elif node.node_type == NodeType.TRANSFORM:
                result = self._run_transform_node(node, outputs)
            elif node.node_type == NodeType.LOOP:
                result = await self._run_loop_node_async(node, outputs, system, graph)
            else:
                result = WorkflowStepResult(
                    node_id=node.id,
                    success=False,
                    output=f"Unknown node type: {node.node_type}",
                )
        except Exception as exc:
            result = WorkflowStepResult(
                node_id=node.id,
                success=False,
                output=f"Node error: {exc}",
            )

        result.duration_seconds = time.time() - t0

        if self._bus:
            self._bus.publish(
                EventType.WORKFLOW_NODE_END,
                {
                    "node": node.id,
                    "success": result.success,
                    "duration": result.duration_seconds,
                },
            )

        return result

    async def _run_agent_node_async(
        self, node: WorkflowNode, outputs: dict[str, str],
        system: Any, graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Execute an agent node, supporting both sync and async system.ask()."""
        input_text = self._get_node_input(node, outputs, graph)
        if system is None:
            return WorkflowStepResult(
                node_id=node.id,
                success=False,
                output="No system available for agent execution.",
            )
        try:
            ask_result = system.ask(
                input_text,
                agent=node.agent or None,
                tools=node.tools or None,
            )
            if inspect.isawaitable(ask_result):
                ask_result = await ask_result
            return WorkflowStepResult(
                node_id=node.id,
                success=True,
                output=ask_result.get("content", ""),
            )
        except Exception as exc:
            return WorkflowStepResult(
                node_id=node.id,
                success=False,
                output=f"Agent error: {exc}",
            )

    async def _run_tool_node_async(
        self, node: WorkflowNode, outputs: dict[str, str], system: Any,
    ) -> WorkflowStepResult:
        """Execute a tool node (async path)."""
        tool_name = node.config.get("tool_name", "")
        tool_args = node.config.get("tool_args", "{}")
        if system and system.tool_executor:
            from openjarvis.core.types import ToolCall

            tc = ToolCall(id=f"wf_{node.id}", name=tool_name, arguments=tool_args)
            execute_fn = system.tool_executor.execute
            if inspect.iscoroutinefunction(execute_fn):
                tr = await execute_fn(tc)
            else:
                tr = execute_fn(tc)
            return WorkflowStepResult(
                node_id=node.id,
                success=tr.success,
                output=tr.content,
            )
        return WorkflowStepResult(
            node_id=node.id,
            success=False,
            output="No tool executor available.",
        )

    async def _run_loop_node_async(
        self, node: WorkflowNode, outputs: dict[str, str],
        system: Any, graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        """Execute a loop node (async path)."""
        input_text = self._get_node_input(node, outputs, graph)
        max_iter = node.max_iterations
        last_output = input_text
        iterations = 0
        for i in range(max_iter):
            iterations = i + 1
            if system:
                ask_result = system.ask(last_output, agent=node.agent or None)
                if inspect.isawaitable(ask_result):
                    ask_result = await ask_result
                last_output = ask_result.get("content", "")
                if (
                    node.condition_expr
                    and node.condition_expr.lower()
                    in last_output.lower()
                ):
                    break
            else:
                break
        return WorkflowStepResult(
            node_id=node.id,
            success=True,
            output=last_output,
            metadata={"iterations": iterations},
        )


__all__ = ["WorkflowEngine"]
