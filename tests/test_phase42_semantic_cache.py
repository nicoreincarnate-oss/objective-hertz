"""Phase 42: Semantic cache safety tests.

CRITICAL: these tests verify the cache NEVER returns code-generation results.
The allowlist is the most important safety invariant in Phase 42.
"""

from __future__ import annotations

import pytest


class TestCacheAllowlist:
    def test_code_generation_in_forbidden(self):
        from shared.semantic_cache import CACHE_FORBIDDEN_OPERATIONS
        assert "code_generation" in CACHE_FORBIDDEN_OPERATIONS
        assert "site_section_generation" in CACHE_FORBIDDEN_OPERATIONS
        assert "html_scaffolding" in CACHE_FORBIDDEN_OPERATIONS
        assert "patch_generation" in CACHE_FORBIDDEN_OPERATIONS
        assert "fix_proposal" in CACHE_FORBIDDEN_OPERATIONS

    def test_email_compose_in_forbidden(self):
        """Personalized emails must NEVER be cached."""
        from shared.semantic_cache import CACHE_FORBIDDEN_OPERATIONS
        assert "email_compose" in CACHE_FORBIDDEN_OPERATIONS
        assert "outreach_draft" in CACHE_FORBIDDEN_OPERATIONS

    def test_aider_calls_forbidden(self):
        from shared.semantic_cache import CACHE_FORBIDDEN_OPERATIONS
        assert "aider_architect" in CACHE_FORBIDDEN_OPERATIONS
        assert "aider_editor" in CACHE_FORBIDDEN_OPERATIONS

    def test_lookups_in_allowlist(self):
        from shared.semantic_cache import CACHEABLE_OPERATIONS
        assert "lead_research_summary" in CACHEABLE_OPERATIONS
        assert "doc_qa" in CACHEABLE_OPERATIONS
        assert "industry_classification" in CACHEABLE_OPERATIONS
        assert "translation" in CACHEABLE_OPERATIONS

    def test_no_overlap_between_allow_and_forbid(self):
        from shared.semantic_cache import CACHEABLE_OPERATIONS, CACHE_FORBIDDEN_OPERATIONS
        overlap = CACHEABLE_OPERATIONS & CACHE_FORBIDDEN_OPERATIONS
        assert not overlap, f"Overlap: {overlap}"


class TestCacheBypass:
    @pytest.mark.asyncio
    async def test_get_returns_none_for_forbidden(self):
        from shared.semantic_cache import SemanticCache
        cache = SemanticCache(redis_client=None)
        result = await cache.get("any prompt", "code_generation")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown_operation(self):
        from shared.semantic_cache import SemanticCache
        cache = SemanticCache(redis_client=None)
        result = await cache.get("any prompt", "made_up_operation_xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_increments_bypass_counter(self):
        from shared.semantic_cache import SemanticCache
        cache = SemanticCache(redis_client=None)
        await cache.get("p", "code_generation")
        await cache.get("p", "made_up")
        assert cache.stats.bypasses == 2
        assert cache.stats.hits == 0


class TestCacheSet:
    @pytest.mark.asyncio
    async def test_set_no_op_for_forbidden(self):
        from shared.semantic_cache import SemanticCache
        cache = SemanticCache(redis_client=None)
        await cache.set("p", "r", "code_generation")
        # Should not crash, should not store anything
        assert cache.stats.bytes_used == 0


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_snapshot_format(self):
        from shared.semantic_cache import SemanticCache
        cache = SemanticCache(redis_client=None)
        snap = await cache.stats_snapshot()
        assert "hits" in snap
        assert "misses" in snap
        assert "bypasses" in snap
        assert "hit_rate" in snap
        assert "estimated_cost_saved_usd" in snap
        assert snap["hit_rate"] == 0.0  # No requests yet
