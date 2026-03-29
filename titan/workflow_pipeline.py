"""Revenue pipeline as an OpenJarvis WorkflowGraph DAG.

The 10-stage pipeline runs as a DAG with conditional skipping and
parallel execution where possible.

Pipeline flow:
    assess → discover → research → compose → send
                                         ↓
                                    follow_up + sync_analytics (parallel)
                                         ↓
                                    close_deal → build → deploy → invoice
"""

from __future__ import annotations

import json
import logging

from openjarvis.workflow.graph import WorkflowGraph
from openjarvis.workflow.types import NodeType, WorkflowEdge, WorkflowNode

logger = logging.getLogger("perseus.titan.workflow")


def build_pipeline_graph() -> WorkflowGraph:
    """Build the revenue pipeline as a WorkflowGraph.

    The graph defines the execution order and parallelism:
    - assess runs first to determine pipeline state
    - follow_up and sync_analytics run in parallel
    - all other stages run sequentially
    """
    g = WorkflowGraph(name="revenue_pipeline")

    # Stage 0: Assess pipeline state
    g.add_node(WorkflowNode(
        id="assess",
        node_type=NodeType.TOOL,
        config={"tool_name": "pipeline_assess"},
    ))

    # Stage 1: Discover leads
    g.add_node(WorkflowNode(
        id="discover",
        node_type=NodeType.TOOL,
        config={"tool_name": "lead_discovery", "tool_args": json.dumps({"batch_size": 20})},
    ))

    # Stage 2: Research leads
    g.add_node(WorkflowNode(
        id="research",
        node_type=NodeType.TOOL,
        config={"tool_name": "lead_research", "tool_args": json.dumps({"batch_size": 10})},
    ))

    # Stage 3: Compose emails
    g.add_node(WorkflowNode(
        id="compose",
        node_type=NodeType.TOOL,
        config={"tool_name": "email_compose", "tool_args": json.dumps({"batch_size": 20})},
    ))

    # Stage 4: Send emails
    g.add_node(WorkflowNode(
        id="send",
        node_type=NodeType.TOOL,
        config={"tool_name": "email_send", "tool_args": json.dumps({"batch_size": 50})},
    ))

    # Stage 5a: Follow up (parallel with sync)
    g.add_node(WorkflowNode(
        id="follow_up",
        node_type=NodeType.TOOL,
        config={"tool_name": "follow_up"},
    ))

    # Stage 5b: Sync analytics (parallel with follow_up)
    g.add_node(WorkflowNode(
        id="sync_analytics",
        node_type=NodeType.TOOL,
        config={"tool_name": "sync_analytics"},
    ))

    # Stage 6: Close interested leads
    g.add_node(WorkflowNode(
        id="close",
        node_type=NodeType.TOOL,
        config={"tool_name": "close_deal"},
    ))

    # Stage 7: Build sites
    g.add_node(WorkflowNode(
        id="build",
        node_type=NodeType.TOOL,
        config={"tool_name": "build_sites"},
    ))

    # Stage 8: Deploy sites
    g.add_node(WorkflowNode(
        id="deploy",
        node_type=NodeType.TOOL,
        config={"tool_name": "deploy_sites"},
    ))

    # Stage 9: Invoice + payments
    g.add_node(WorkflowNode(
        id="invoice",
        node_type=NodeType.TOOL,
        config={"tool_name": "process_invoices"},
    ))

    # ── Edges (execution order) ──────────────────────────────────

    # Linear chain: assess → discover → research → compose → send
    g.add_edge(WorkflowEdge(source="assess", target="discover"))
    g.add_edge(WorkflowEdge(source="discover", target="research"))
    g.add_edge(WorkflowEdge(source="research", target="compose"))
    g.add_edge(WorkflowEdge(source="compose", target="send"))

    # After send: follow_up and sync_analytics run in parallel
    g.add_edge(WorkflowEdge(source="send", target="follow_up"))
    g.add_edge(WorkflowEdge(source="send", target="sync_analytics"))

    # After both parallel stages complete: close → build → deploy → invoice
    g.add_edge(WorkflowEdge(source="follow_up", target="close"))
    g.add_edge(WorkflowEdge(source="sync_analytics", target="close"))
    g.add_edge(WorkflowEdge(source="close", target="build"))
    g.add_edge(WorkflowEdge(source="build", target="deploy"))
    g.add_edge(WorkflowEdge(source="deploy", target="invoice"))

    # Validate
    valid, msg = g.validate()
    if not valid:
        logger.error("Pipeline graph validation failed: %s", msg)
        raise ValueError(f"Invalid pipeline graph: {msg}")

    logger.info(
        "Pipeline graph built: %d nodes, %d edges, %d stages",
        len(g.nodes), len(g.edges), len(g.execution_stages()),
    )
    return g


async def run_pipeline():
    """Execute the full revenue pipeline via WorkflowEngine (async).

    This is the main entry point for running the pipeline as a DAG
    instead of the old sequential loop.  Uses ``run_async()`` for
    efficient async I/O on parallel stages.
    """
    from shared.oj_bridge import get_workflow_engine
    from titan.workflow_tools import PIPELINE_TOOLS

    graph = build_pipeline_graph()
    engine = get_workflow_engine()

    # Build a minimal system-like object with tool_executor for TOOL nodes
    from openjarvis.tools._stubs import ToolExecutor
    tool_executor = ToolExecutor(PIPELINE_TOOLS)

    class _PipelineSystem:
        def __init__(self):
            self.tool_executor = tool_executor

    result = await engine.run_async(graph, _PipelineSystem(), initial_input="")

    logger.info(
        "Pipeline complete: success=%s, stages=%d, duration=%.1fs",
        result.success,
        len(result.steps),
        result.total_duration_seconds,
    )

    for step in result.steps:
        status = "OK" if step.success else "FAIL"
        logger.info(
            "  [%s] %s (%.1fs): %s",
            status, step.node_id, step.duration_seconds, step.output[:200],
        )

    return result


def run_pipeline_sync():
    """Synchronous wrapper for ``run_pipeline()``.

    Use this from callers that cannot use ``await`` (e.g. CLI scripts).
    Existing callers that used the old sync ``run_pipeline()`` should
    migrate to ``await run_pipeline()`` or use this wrapper.
    """
    import asyncio

    return asyncio.run(run_pipeline())
