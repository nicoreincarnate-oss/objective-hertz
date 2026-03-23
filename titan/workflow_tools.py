"""Pipeline stage tools — wraps each Titan pipeline function as an OJ BaseTool.

These tools are used by the WorkflowEngine to execute pipeline stages
within a DAG. Each tool calls the corresponding async pipeline function
via asyncio.run() since WorkflowEngine uses ThreadPoolExecutor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger("perseus.titan.workflow_tools")


def _run_async(coro):
    """Run an async coroutine from synchronous context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=600)
    return asyncio.run(coro)


class PipelineAssessTool(BaseTool):
    tool_id = "pipeline_assess"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="pipeline_assess",
            description="Assess current pipeline state (lead counts by status, pending work).",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from shared.pipeline import assess_pipeline_state
        result = _run_async(assess_pipeline_state())
        return ToolResult(tool_name=self.tool_id, content=json.dumps(result, default=str), success=True)


class LeadDiscoveryTool(BaseTool):
    tool_id = "lead_discovery"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="lead_discovery",
            description="Discover new business leads without websites.",
            parameters={"type": "object", "properties": {"batch_size": {"type": "integer", "default": 20}}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.lead_discovery import discover_leads
        batch = params.get("batch_size", 20)
        ids = _run_async(discover_leads(batch_size=batch))
        return ToolResult(tool_name=self.tool_id, content=json.dumps({"discovered": len(ids), "ids": ids}), success=True)


class LeadResearchTool(BaseTool):
    tool_id = "lead_research"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="lead_research",
            description="Research discovered leads — scrape, score, extract facts.",
            parameters={"type": "object", "properties": {"batch_size": {"type": "integer", "default": 10}}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.lead_research import research_leads
        _run_async(research_leads(batch_size=params.get("batch_size", 10)))
        return ToolResult(tool_name=self.tool_id, content='{"researched": true}', success=True)


class EmailComposeTool(BaseTool):
    tool_id = "email_compose"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="email_compose",
            description="Compose personalized cold emails for researched leads.",
            parameters={"type": "object", "properties": {"batch_size": {"type": "integer", "default": 20}}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.email_compose import compose_emails
        _run_async(compose_emails(batch_size=params.get("batch_size", 20)))
        return ToolResult(tool_name=self.tool_id, content='{"composed": true}', success=True)


class EmailSendTool(BaseTool):
    tool_id = "email_send"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="email_send",
            description="Send queued emails via Instantly campaign.",
            parameters={"type": "object", "properties": {"batch_size": {"type": "integer", "default": 50}}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.email_send import send_emails
        _run_async(send_emails(batch_size=params.get("batch_size", 50)))
        return ToolResult(tool_name=self.tool_id, content='{"sent": true}', success=True)


class FollowUpTool(BaseTool):
    tool_id = "follow_up"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="follow_up",
            description="Process replies and send follow-up sequences.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.follow_up import process_follow_ups
        _run_async(process_follow_ups())
        return ToolResult(tool_name=self.tool_id, content='{"followed_up": true}', success=True)


class CloseDealTool(BaseTool):
    tool_id = "close_deal"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="close_deal",
            description="Build demo sites and send proposals to interested leads.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.close_deal import process_interested_leads
        _run_async(process_interested_leads())
        return ToolResult(tool_name=self.tool_id, content='{"closed": true}', success=True)


class BuildSitesTool(BaseTool):
    tool_id = "build_sites"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="build_sites",
            description="Build full websites for closed deals.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.build_site import build_sites
        _run_async(build_sites())
        return ToolResult(tool_name=self.tool_id, content='{"built": true}', success=True)


class DeploySitesTool(BaseTool):
    tool_id = "deploy_sites"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="deploy_sites",
            description="Verify deployed sites are live.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.deploy_site import deploy_sites
        _run_async(deploy_sites())
        return ToolResult(tool_name=self.tool_id, content='{"deployed": true}', success=True)


class InvoiceTool(BaseTool):
    tool_id = "process_invoices"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="process_invoices",
            description="Send invoices and track payments.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.invoice import process_invoices
        _run_async(process_invoices())
        return ToolResult(tool_name=self.tool_id, content='{"invoiced": true}', success=True)


class SyncAnalyticsTool(BaseTool):
    tool_id = "sync_analytics"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="sync_analytics",
            description="Sync campaign analytics from Instantly.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from titan.pipeline.email_send import sync_campaign_analytics
        _run_async(sync_campaign_analytics())
        return ToolResult(tool_name=self.tool_id, content='{"synced": true}', success=True)


# Registry of all pipeline tools
PIPELINE_TOOLS: list[BaseTool] = [
    PipelineAssessTool(),
    LeadDiscoveryTool(),
    LeadResearchTool(),
    EmailComposeTool(),
    EmailSendTool(),
    FollowUpTool(),
    CloseDealTool(),
    BuildSitesTool(),
    DeploySitesTool(),
    InvoiceTool(),
    SyncAnalyticsTool(),
]
