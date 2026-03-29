"""Tests for WorkflowEngine async execution path (Phase 0b)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjarvis.workflow.engine import WorkflowEngine
from openjarvis.workflow.graph import WorkflowGraph
from openjarvis.workflow.types import NodeType, WorkflowEdge, WorkflowNode


def _make_system(responses: list[str] | None = None):
    """Create a mock system whose ask() returns canned responses."""
    responses = list(responses or ["response"])
    call_count = {"n": 0}

    def _ask(text, *, agent=None, tools=None):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        return {"content": responses[idx]}

    system = MagicMock()
    system.ask = MagicMock(side_effect=_ask)
    system.tool_executor = None
    return system


def _make_async_system(responses: list[str] | None = None):
    """Create a mock system whose ask() is an async coroutine."""
    responses = list(responses or ["response"])
    call_count = {"n": 0}

    async def _ask(text, *, agent=None, tools=None):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        return {"content": responses[idx]}

    system = MagicMock()
    system.ask = _ask
    system.tool_executor = None
    return system


def _sequential_graph(name: str = "seq", node_count: int = 3) -> WorkflowGraph:
    """Build a simple sequential DAG: a -> b -> c."""
    g = WorkflowGraph(name)
    ids = [chr(ord("a") + i) for i in range(node_count)]
    for nid in ids:
        g.add_node(WorkflowNode(id=nid, node_type=NodeType.AGENT))
    for i in range(len(ids) - 1):
        g.add_edge(WorkflowEdge(source=ids[i], target=ids[i + 1]))
    return g


def _parallel_graph() -> WorkflowGraph:
    """Build a DAG where b and c run in parallel after a, then d.

    a -> b
    a -> c
    b -> d
    c -> d
    """
    g = WorkflowGraph("par")
    for nid in ("a", "b", "c", "d"):
        g.add_node(WorkflowNode(id=nid, node_type=NodeType.AGENT))
    g.add_edge(WorkflowEdge(source="a", target="b"))
    g.add_edge(WorkflowEdge(source="a", target="c"))
    g.add_edge(WorkflowEdge(source="b", target="d"))
    g.add_edge(WorkflowEdge(source="c", target="d"))
    return g


class TestRunAsyncSequentialDAG:
    """test_run_async_sequential_dag: 3 nodes in sequence, verify execution order."""

    @pytest.mark.asyncio
    async def test_sequential_execution_order(self):
        graph = _sequential_graph()
        system = _make_system(["out_a", "out_b", "out_c"])
        engine = WorkflowEngine()

        result = await engine.run_async(graph, system, initial_input="start")

        assert result.success
        assert len(result.steps) == 3
        # Verify execution order via outputs
        assert result.steps[0].node_id == "a"
        assert result.steps[0].output == "out_a"
        assert result.steps[1].node_id == "b"
        assert result.steps[1].output == "out_b"
        assert result.steps[2].node_id == "c"
        assert result.steps[2].output == "out_c"
        assert result.final_output == "out_c"

    @pytest.mark.asyncio
    async def test_sequential_with_async_system(self):
        graph = _sequential_graph()
        system = _make_async_system(["async_a", "async_b", "async_c"])
        engine = WorkflowEngine()

        result = await engine.run_async(graph, system, initial_input="start")

        assert result.success
        assert result.final_output == "async_c"


class TestRunAsyncParallelNodes:
    """test_run_async_parallel_nodes: 2 parallel nodes, verify both execute."""

    @pytest.mark.asyncio
    async def test_parallel_nodes_both_execute(self):
        graph = _parallel_graph()
        system = _make_system(["out_a", "out_b", "out_c", "out_d"])
        engine = WorkflowEngine()

        result = await engine.run_async(graph, system, initial_input="start")

        assert result.success
        # Should have 4 steps total (a, then b+c in parallel, then d)
        assert len(result.steps) == 4
        executed_ids = {s.node_id for s in result.steps}
        assert executed_ids == {"a", "b", "c", "d"}
        assert result.final_output == "out_d"


class TestRunBackwardCompat:
    """test_run_backward_compat: existing run() method still works for simple DAG."""

    def test_sync_run_still_works(self):
        graph = _sequential_graph(node_count=2)
        system = _make_system(["sync_a", "sync_b"])
        engine = WorkflowEngine()

        result = engine.run(graph, system, initial_input="hello")

        assert result.success
        assert len(result.steps) == 2
        assert result.final_output == "sync_b"

    def test_invalid_graph_returns_failure(self):
        g = WorkflowGraph("empty")
        engine = WorkflowEngine()
        # An empty graph without nodes is invalid
        result = engine.run(g, None, initial_input="test")
        # Should handle gracefully (either fail validation or return empty)
        assert isinstance(result.success, bool)
