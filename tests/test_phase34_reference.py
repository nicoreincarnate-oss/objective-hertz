"""Tests for clawdbot.reference_manager — Reference screenshot manager."""

from __future__ import annotations

import pytest

from clawdbot.reference_manager import ReferenceManager

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-reference-png"


# ---------------------------------------------------------------------------
# ReferenceManager
# ---------------------------------------------------------------------------


class TestReferenceManager:
    @pytest.fixture
    def ref_dir(self, tmp_path):
        """Create a temporary references directory."""
        return tmp_path / "references"

    @pytest.fixture
    def manager(self, ref_dir):
        return ReferenceManager(base_dir=ref_dir)

    def test_ref_path(self, manager):
        path = manager._ref_path("hero", "cosmos-dark-curation")
        assert path.name == "cosmos-dark-curation.png"
        assert path.parent.name == "hero"

    def test_ref_path_normalizes_spaces(self, manager):
        path = manager._ref_path("Above Fold", "Bold Editorial")
        assert path.parent.name == "above-fold"
        assert path.name == "bold-editorial.png"

    @pytest.mark.asyncio
    async def test_get_missing_reference_returns_none(self, manager):
        result = await manager.get_section_reference("hero", "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_existing_reference(self, manager, ref_dir):
        # Pre-populate a reference
        hero_dir = ref_dir / "hero"
        hero_dir.mkdir(parents=True)
        (hero_dir / "cosmos-dark-curation.png").write_bytes(FAKE_PNG)

        result = await manager.get_section_reference("hero", "cosmos-dark-curation")
        assert result == FAKE_PNG

    @pytest.mark.asyncio
    async def test_list_references_empty(self, manager):
        result = await manager.list_references()
        assert result == {}

    @pytest.mark.asyncio
    async def test_list_references_populated(self, manager, ref_dir):
        # Create some references
        for section in ("hero", "features"):
            d = ref_dir / section
            d.mkdir(parents=True)
            (d / "minimal-geometric.png").write_bytes(FAKE_PNG)

        (ref_dir / "hero" / "bold-editorial.png").write_bytes(FAKE_PNG)

        result = await manager.list_references()
        assert "hero" in result
        assert "features" in result
        assert len(result["hero"]) == 2
        assert "minimal-geometric" in result["hero"]
        assert "bold-editorial" in result["hero"]
        assert len(result["features"]) == 1

    @pytest.mark.asyncio
    async def test_list_ignores_non_directories(self, manager, ref_dir):
        ref_dir.mkdir(parents=True)
        (ref_dir / "README.md").write_text("ignore me")

        result = await manager.list_references()
        assert result == {}

    def test_update_metadata_creates_file(self, manager, ref_dir):
        section_dir = ref_dir / "hero"
        section_dir.mkdir(parents=True)

        manager._update_metadata("hero", "cosmos-dark-curation", source_url="https://example.com")

        meta_path = section_dir / "metadata.yaml"
        assert meta_path.exists()
        content = meta_path.read_text()
        assert "cosmos-dark-curation" in content

    @pytest.mark.asyncio
    async def test_capture_reference_stores_png(self):
        """Test capture_reference with mocked Playwright."""
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.screenshot = AsyncMock(return_value=FAKE_PNG)

        browser = AsyncMock()
        browser.new_page = AsyncMock(return_value=page)
        browser.close = AsyncMock()

        chromium = AsyncMock()
        chromium.launch = AsyncMock(return_value=browser)

        pw_inst = AsyncMock()
        pw_inst.chromium = chromium

        pw_cm = AsyncMock()
        pw_cm.__aenter__ = AsyncMock(return_value=pw_inst)
        pw_cm.__aexit__ = AsyncMock(return_value=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ReferenceManager(base_dir=Path(tmpdir) / "refs")

            with patch("clawdbot.reference_manager.async_playwright", return_value=pw_cm):
                await manager.capture_reference(
                    "https://example.com",
                    "hero",
                    "minimal-geometric",
                )

            stored = Path(tmpdir) / "refs" / "hero" / "minimal-geometric.png"
            assert stored.exists()
            assert stored.read_bytes() == FAKE_PNG
