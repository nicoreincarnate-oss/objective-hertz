"""Tests for 21st.dev Magic MCP client wrapper."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_module():
    """Import the module under test (avoids top-level import issues)."""
    from tools import twentyfirst_dev
    return twentyfirst_dev


# ---------------------------------------------------------------------------
# test_availability_check
# ---------------------------------------------------------------------------

class TestAvailabilityCheck:
    """twentyfirst_available() should reflect env + node presence."""

    @patch.dict(os.environ, {"TWENTYFIRST_API_KEY": "sk-test"})
    @patch("shutil.which", return_value="/usr/local/bin/node")
    def test_available_when_configured(self, _which):
        mod = _import_module()
        assert mod.twentyfirst_available() is True

    @patch.dict(os.environ, {}, clear=True)
    @patch("shutil.which", return_value="/usr/local/bin/node")
    def test_unavailable_without_key(self, _which):
        mod = _import_module()
        # Ensure the key is truly absent
        os.environ.pop("TWENTYFIRST_API_KEY", None)
        assert mod.twentyfirst_available() is False


# ---------------------------------------------------------------------------
# test_connect_no_key
# ---------------------------------------------------------------------------

class TestConnectNoKey:
    """connect() should return False when API key is missing."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {}, clear=True)
    async def test_connect_returns_false_without_key(self):
        mod = _import_module()
        os.environ.pop("TWENTYFIRST_API_KEY", None)
        client = mod.TwentyFirstDev()
        result = await client.connect()
        assert result is False
        assert client._session is None


# ---------------------------------------------------------------------------
# test_connect_no_node
# ---------------------------------------------------------------------------

class TestConnectNoNode:
    """twentyfirst_available() should return False when Node.js is missing."""

    @patch.dict(os.environ, {"TWENTYFIRST_API_KEY": "sk-test"})
    @patch("shutil.which", return_value=None)
    def test_unavailable_without_node(self, _which):
        mod = _import_module()
        assert mod.twentyfirst_available() is False


# ---------------------------------------------------------------------------
# test_generate_component_mock
# ---------------------------------------------------------------------------

class TestGenerateComponentMock:
    """generate_component() with a mocked MCP session."""

    @pytest.mark.asyncio
    async def test_generate_returns_code(self):
        mod = _import_module()
        client = mod.TwentyFirstDev()

        # Build a fake MCP result
        fake_content_item = MagicMock()
        fake_content_item.text = '<div className="hero">Hello</div>'
        fake_result = MagicMock()
        fake_result.content = [fake_content_item]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=fake_result)

        # Inject the mock session so connect() is skipped
        client._session = mock_session

        code = await client.generate_component(
            description="A hero section with a big headline",
            search_query="hero headline",
            project_dir="/tmp/test-project",
            file_path="/tmp/test-project/components/Hero.tsx",
        )

        assert code == '<div className="hero">Hello</div>'
        mock_session.call_tool.assert_called_once_with(
            "21st_magic_component_builder",
            arguments={
                "message": "A hero section with a big headline",
                "searchQuery": "hero headline",
                "standaloneRequestQuery": "A hero section with a big headline",
                "absolutePathToCurrentFile": "/tmp/test-project/components/Hero.tsx",
                "absolutePathToProjectDirectory": "/tmp/test-project",
            },
        )


# ---------------------------------------------------------------------------
# test_batch_generation_mock
# ---------------------------------------------------------------------------

class TestBatchGenerationMock:
    """generate_site_sections() should call generate_component for each section."""

    @pytest.mark.asyncio
    async def test_batch_generates_all_sections(self):
        mod = _import_module()

        # Reset the singleton so we control the client
        mod._client = None

        fake_content_item = MagicMock()
        fake_content_item.text = "<section>Generated</section>"
        fake_result = MagicMock()
        fake_result.content = [fake_content_item]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=fake_result)
        mock_session.initialize = AsyncMock()

        client = mod.TwentyFirstDev()
        client._session = mock_session
        mod._client = client

        # Patch connect to return True (session already set)
        with patch.object(client, "connect", return_value=True):
            sections = [
                {
                    "name": "Hero",
                    "description": "A hero section",
                    "search_query": "hero cta",
                },
                {
                    "name": "Features",
                    "description": "A features grid",
                    "search_query": "features grid",
                },
            ]
            results = await mod.generate_site_sections(
                sections, project_dir="/tmp/batch-test"
            )

        assert len(results) == 2
        assert results["Hero"] == "<section>Generated</section>"
        assert results["Features"] == "<section>Generated</section>"
        assert mock_session.call_tool.call_count == 2

        # Clean up singleton
        mod._client = None


# ---------------------------------------------------------------------------
# test_graceful_failure
# ---------------------------------------------------------------------------

class TestGracefulFailure:
    """All methods should return None (not raise) on errors."""

    @pytest.mark.asyncio
    async def test_generate_returns_none_on_exception(self):
        mod = _import_module()
        client = mod.TwentyFirstDev()

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=RuntimeError("MCP server crashed")
        )
        client._session = mock_session

        result = await client.generate_component("test", "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_inspiration_returns_none_on_exception(self):
        mod = _import_module()
        client = mod.TwentyFirstDev()

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=ConnectionError("pipe broken")
        )
        client._session = mock_session

        result = await client.get_inspiration("test", "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_refine_returns_none_on_exception(self):
        mod = _import_module()
        client = mod.TwentyFirstDev()

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(side_effect=TimeoutError("timeout"))
        client._session = mock_session

        result = await client.refine_component("fix it", "ctx", "/tmp/f.tsx")
        assert result is None

    @pytest.mark.asyncio
    async def test_logos_returns_none_on_exception(self):
        mod = _import_module()
        client = mod.TwentyFirstDev()

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(side_effect=OSError("nope"))
        client._session = mock_session

        result = await client.search_logos(["acme"])
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_returns_none_when_no_content(self):
        mod = _import_module()
        client = mod.TwentyFirstDev()

        fake_result = MagicMock()
        fake_result.content = []

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=fake_result)
        client._session = mock_session

        result = await client.generate_component("test", "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_disconnect_is_safe_when_not_connected(self):
        mod = _import_module()
        client = mod.TwentyFirstDev()
        # Should not raise
        await client.disconnect()
        assert client._session is None
