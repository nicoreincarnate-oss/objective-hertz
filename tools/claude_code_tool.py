"""Claude Agent SDK wrapper for autonomous code generation.

Used by ClawdBot for site building, code generation, and complex
multi-step tasks that benefit from Claude Code's tool-use loop.
"""

import os
import json
import logging
from typing import Optional, AsyncIterator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def agent_sdk_available() -> bool:
    """Check if Claude Agent SDK is configured."""
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        import claude_agent_sdk
        has_sdk = True
    except ImportError:
        has_sdk = False
    return has_key and has_sdk


@dataclass
class AgentResult:
    """Result from an Agent SDK execution."""
    success: bool
    output: str = ""
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    turns_used: int = 0
    error: str = ""


async def run_agent_task(
    prompt: str,
    cwd: str = "/tmp/perseus-workspace",
    system_prompt: str = "",
    model: str = "claude-sonnet-4-6",
    max_budget_usd: float = 2.0,
    max_turns: int = 50,
    allowed_tools: list[str] = None,
    custom_tools: dict = None,
    mcp_servers: dict = None,
) -> AgentResult:
    """
    Run an autonomous agent task using the Claude Agent SDK.

    Args:
        prompt: What the agent should do
        cwd: Working directory for the agent
        system_prompt: Custom system prompt (appended to default)
        model: Model to use
        max_budget_usd: Hard budget cap for this task
        max_turns: Maximum agent loop iterations
        allowed_tools: Pre-approved tools (no permission prompt)
        custom_tools: Dict of custom MCP tool servers
        mcp_servers: External MCP server configs

    Returns:
        AgentResult with output, files changed, cost, etc.
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions
    except ImportError:
        logger.error("claude-agent-sdk not installed. Run: pip install claude-agent-sdk")
        return AgentResult(success=False, error="claude-agent-sdk not installed")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return AgentResult(success=False, error="ANTHROPIC_API_KEY not set")

    # Ensure working directory exists
    os.makedirs(cwd, exist_ok=True)

    if allowed_tools is None:
        allowed_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    options = ClaudeAgentOptions(
        model=model,
        allowed_tools=allowed_tools,
        permission_mode="acceptEdits",
        cwd=cwd,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
    )

    if system_prompt:
        options.system_prompt = system_prompt

    if mcp_servers:
        options.mcp_servers = mcp_servers

    if custom_tools:
        if not hasattr(options, 'mcp_servers') or options.mcp_servers is None:
            options.mcp_servers = {}
        options.mcp_servers.update(custom_tools)

    result = AgentResult(success=False)

    try:
        logger.info(f"Agent SDK: Starting task in {cwd} (budget: ${max_budget_usd})")

        async for message in query(prompt=prompt, options=options):
            # Process messages from the agent
            msg_type = getattr(message, 'type', None) or type(message).__name__

            if msg_type == 'ResultMessage' or hasattr(message, 'result'):
                result.success = True
                result.output = getattr(message, 'result', str(message))
                result.cost_usd = getattr(message, 'total_cost_usd', 0.0)
                result.turns_used = getattr(message, 'num_turns', 0)
            elif msg_type == 'ToolUseMessage' or hasattr(message, 'tool_name'):
                tool_name = getattr(message, 'tool_name', '')
                if tool_name in ('Write', 'Edit'):
                    file_path = getattr(message, 'file_path', '')
                    if file_path:
                        if tool_name == 'Write':
                            result.files_created.append(file_path)
                        else:
                            result.files_modified.append(file_path)

        logger.info(
            f"Agent SDK: Task {'succeeded' if result.success else 'failed'} "
            f"(cost: ${result.cost_usd:.4f}, turns: {result.turns_used}, "
            f"files: {len(result.files_created)} created, {len(result.files_modified)} modified)"
        )

    except Exception as e:
        logger.error(f"Agent SDK error: {e}")
        result.error = str(e)

    return result


async def build_site(
    brief: dict,
    output_dir: str = "/tmp/perseus-site",
    use_21st_dev: bool = True,
) -> AgentResult:
    """
    Build a complete website using the Agent SDK.

    Args:
        brief: Site brief with keys: business_name, industry, location,
               services, tone, color_preferences, pages
        output_dir: Where to generate the site files

        use_21st_dev: Whether to include 21st.dev MCP for components

    Returns:
        AgentResult with generated site files
    """
    business = brief.get("business_name", "Business")
    industry = brief.get("industry", "")
    services = brief.get("services", [])
    pages = brief.get("pages", ["home", "services", "about", "contact"])
    tone = brief.get("tone", "professional and trustworthy")

    prompt = f"""Build a complete, production-ready website for {business}.

Industry: {industry}
Services: {', '.join(services) if services else 'general services'}
Pages needed: {', '.join(pages)}
Tone: {tone}

Requirements:
1. Use React with TypeScript and Tailwind CSS
2. Use shadcn/ui components where appropriate
3. Make it fully responsive (mobile, tablet, desktop)
4. Include proper meta tags and SEO structure
5. Use professional typography and color scheme appropriate for {industry}
6. Include a hero section, services grid, testimonials placeholder, and contact form
7. All pages should be complete — no placeholder "lorem ipsum" text
8. Create actual content appropriate for a {industry} business
9. Write all files to the current working directory

Start by creating the project structure, then build each component."""

    system_prompt = """You are ClawdBot, Perseus's site building agent. You build \
high-quality, professional websites that real businesses would be proud to use.
Focus on visual polish, mobile responsiveness, and professional content.
Do NOT use placeholder text — write real copy for the business."""

    mcp_servers = {}
    if use_21st_dev and os.environ.get("TWENTYFIRST_API_KEY"):
        mcp_servers["@21st-dev/magic"] = {
            "command": "npx",
            "args": ["-y", "@21st-dev/magic@latest"],
            "env": {"API_KEY": os.environ["TWENTYFIRST_API_KEY"]}
        }

    allowed_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
    if mcp_servers:
        allowed_tools.extend([
            "mcp__@21st-dev/magic__21st_magic_component_builder",
            "mcp__@21st-dev/magic__21st_magic_component_inspiration",
        ])

    return await run_agent_task(
        prompt=prompt,
        cwd=output_dir,
        system_prompt=system_prompt,
        model="claude-sonnet-4-6",
        max_budget_usd=3.0,
        max_turns=100,
        allowed_tools=allowed_tools,
        mcp_servers=mcp_servers,
    )


async def generate_code(
    task: str,
    cwd: str,
    max_budget_usd: float = 1.0,
) -> AgentResult:
    """
    General-purpose code generation task.
    Used for fixes, refactoring, or generating specific modules.
    """
    return await run_agent_task(
        prompt=task,
        cwd=cwd,
        max_budget_usd=max_budget_usd,
        max_turns=30,
    )
