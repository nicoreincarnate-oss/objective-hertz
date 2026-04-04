"""21st.dev Magic MCP client for UI component generation.

Connects to the @21st-dev/magic MCP server via stdio transport.
Generates production-ready React/TypeScript components from descriptions.
Used by ClawdBot's site_builder for high-quality site generation.
"""

import os
import logging
import asyncio
import json
import subprocess
import shutil
from typing import Optional

logger = logging.getLogger(__name__)


def twentyfirst_available() -> bool:
    """Check if 21st.dev MCP is configured and Node.js is available."""
    has_key = bool(os.environ.get("TWENTYFIRST_API_KEY"))
    has_node = shutil.which("node") is not None
    has_npx = shutil.which("npx") is not None
    if not has_key:
        logger.debug("TWENTYFIRST_API_KEY not set")
    if not has_node or not has_npx:
        logger.debug("Node.js/npx not found")
    return has_key and has_node and has_npx


class TwentyFirstDev:
    """Client for 21st.dev Magic MCP component generation."""

    def __init__(self):
        self._session = None
        self._client_ctx = None
        self._session_ctx = None

    async def connect(self):
        """Start the MCP server and establish connection."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            logger.warning("mcp SDK not installed. Run: pip install mcp")
            return False

        api_key = os.environ.get("TWENTYFIRST_API_KEY", "")
        if not api_key:
            logger.warning("TWENTYFIRST_API_KEY not set")
            return False

        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@21st-dev/magic@latest"],
            env={**os.environ, "API_KEY": api_key},
        )

        try:
            self._client_ctx = stdio_client(server_params)
            read, write = await self._client_ctx.__aenter__()
            self._session_ctx = ClientSession(read, write)
            self._session = await self._session_ctx.__aenter__()
            await self._session.initialize()
            logger.info("21st.dev MCP connected")
            return True
        except Exception as e:
            logger.error(f"21st.dev MCP connection failed: {e}")
            await self.disconnect()
            return False

    async def disconnect(self):
        """Shut down the MCP server connection."""
        try:
            if self._session_ctx:
                await self._session_ctx.__aexit__(None, None, None)
            if self._client_ctx:
                await self._client_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        self._session = None
        self._session_ctx = None
        self._client_ctx = None

    async def generate_component(
        self,
        description: str,
        search_query: str,
        project_dir: str = "/tmp/perseus-site",
        file_path: str = "/tmp/perseus-site/components/generated.tsx",
    ) -> Optional[str]:
        """
        Generate a React component from a natural language description.

        Args:
            description: What the component should look like/do
            search_query: 2-4 word search query for component inspiration
            project_dir: Absolute path to the project directory
            file_path: Absolute path where the component file will be saved

        Returns:
            Generated React/TypeScript component code, or None on failure
        """
        if not self._session:
            if not await self.connect():
                return None

        try:
            result = await self._session.call_tool(
                "21st_magic_component_builder",
                arguments={
                    "message": description,
                    "searchQuery": search_query,
                    "standaloneRequestQuery": description,
                    "absolutePathToCurrentFile": file_path,
                    "absolutePathToProjectDirectory": project_dir,
                },
            )
            if result and result.content:
                return result.content[0].text
            return None
        except Exception as e:
            logger.error(f"21st.dev component generation failed: {e}")
            return None

    async def get_inspiration(
        self,
        description: str,
        search_query: str,
    ) -> Optional[str]:
        """
        Fetch component previews/inspiration from the 21st.dev library.
        Does NOT generate code — just returns existing component data.
        """
        if not self._session:
            if not await self.connect():
                return None

        try:
            result = await self._session.call_tool(
                "21st_magic_component_inspiration",
                arguments={
                    "message": description,
                    "searchQuery": search_query,
                },
            )
            if result and result.content:
                return result.content[0].text
            return None
        except Exception as e:
            logger.error(f"21st.dev inspiration fetch failed: {e}")
            return None

    async def refine_component(
        self,
        feedback: str,
        context: str,
        file_path: str,
    ) -> Optional[str]:
        """
        Improve an existing component based on feedback.
        """
        if not self._session:
            if not await self.connect():
                return None

        try:
            result = await self._session.call_tool(
                "21st_magic_component_refiner",
                arguments={
                    "userMessage": feedback,
                    "context": context,
                    "absolutePathToRefiningFile": file_path,
                },
            )
            if result and result.content:
                return result.content[0].text
            return None
        except Exception as e:
            logger.error(f"21st.dev refinement failed: {e}")
            return None

    async def search_logos(
        self,
        queries: list[str],
        format: str = "JSX",
    ) -> Optional[str]:
        """
        Search for company logos in JSX/TSX/SVG format.
        """
        if not self._session:
            if not await self.connect():
                return None

        try:
            result = await self._session.call_tool(
                "logo_search",
                arguments={
                    "queries": queries,
                    "format": format,
                },
            )
            if result and result.content:
                return result.content[0].text
            return None
        except Exception as e:
            logger.error(f"21st.dev logo search failed: {e}")
            return None


# Module-level convenience functions
_client: Optional[TwentyFirstDev] = None


async def get_client() -> TwentyFirstDev:
    """Get or create the singleton 21st.dev client."""
    global _client
    if _client is None:
        _client = TwentyFirstDev()
    return _client


async def generate_component(
    description: str, search_query: str, **kwargs
) -> Optional[str]:
    """Convenience: generate a component."""
    client = await get_client()
    return await client.generate_component(description, search_query, **kwargs)


async def generate_site_sections(
    sections: list[dict],
    project_dir: str = "/tmp/perseus-site",
) -> dict[str, Optional[str]]:
    """
    Generate multiple site sections in batch.

    Args:
        sections: List of dicts with 'name', 'description', 'search_query'
        project_dir: Project directory for component generation

    Returns:
        Dict mapping section name to generated code (or None if failed)
    """
    client = await get_client()
    if not await client.connect():
        return {s["name"]: None for s in sections}

    results = {}
    for section in sections:
        file_path = f"{project_dir}/components/{section['name']}.tsx"
        code = await client.generate_component(
            description=section["description"],
            search_query=section["search_query"],
            project_dir=project_dir,
            file_path=file_path,
        )
        results[section["name"]] = code
        logger.info(
            f"21st.dev: {'Generated' if code else 'Failed'} {section['name']}"
        )

    return results
