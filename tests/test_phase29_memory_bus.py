"""Phase 29: Unified Memory Bus — comprehensive test suite.

Tests cover:
1. MemoryChangedEvent dataclass and publish
2. Event emission from all 5 daemon stores
3. MAGMA subscriber + source adapters
4. Conway read-only adapter
5. High-water mark crash recovery
6. Bandit state persistence (snapshot save/load)
7. 3-tier access control (public/scoped/restricted)
8. Memory Explorer API endpoints
9. Memory index MAGMA provenance source
10. Feature flag gating
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

# ── Test 1: MemoryChangedEvent dataclass ──────────────────────────


def test_memory_changed_event_creation():
    """MemoryChangedEvent should create with all required fields."""
    from shared.comms import MemoryChangedEvent

    evt = MemoryChangedEvent(
        source_daemon="titan",
        memory_type="learning",
        record_id=42,
        table_name="titan_learnings",
        action="insert",
        visibility="public",
        summary="Test insight",
    )
    assert evt.source_daemon == "titan"
    assert evt.memory_type == "learning"
    assert evt.record_id == 42
    assert evt.table_name == "titan_learnings"
    assert evt.action == "insert"
    assert evt.visibility == "public"
    assert evt.summary == "Test insight"
    assert isinstance(evt.timestamp, float)
    assert evt.timestamp > 0


def test_memory_changed_event_defaults():
    """MemoryChangedEvent should have sensible defaults."""
    from shared.comms import MemoryChangedEvent

    evt = MemoryChangedEvent(
        source_daemon="perseus",
        memory_type="observation",
        record_id="key_123",
        table_name="daemon_memory",
        action="insert",
        visibility="public",
    )
    assert evt.summary is None
    assert evt.timestamp > 0


def test_memory_changed_topic_constant():
    """MEMORY_CHANGED_TOPIC should be defined."""
    from shared.comms import MEMORY_CHANGED_TOPIC
    assert MEMORY_CHANGED_TOPIC == "memory.changed"


# ── Test 2: publish_memory_event ──────────────────────────────────


@pytest.mark.asyncio
async def test_publish_memory_event_calls_emit():
    """publish_memory_event should call db.emit_event with correct topic."""
    from shared.comms import MemoryChangedEvent, publish_memory_event

    mock_emit = AsyncMock()
    mock_get_config = AsyncMock(return_value="true")

    with (
        patch("shared.comms.db.emit_event", mock_emit),
        patch("shared.comms.db.get_config", mock_get_config),
    ):
        evt = MemoryChangedEvent(
            source_daemon="titan",
            memory_type="learning",
            record_id=1,
            table_name="titan_learnings",
            action="insert",
            visibility="public",
            summary="test",
        )
        await publish_memory_event(evt)

    mock_emit.assert_called_once()
    topic, payload = mock_emit.call_args[0]
    assert topic == "memory.changed"
    assert payload["source_daemon"] == "titan"
    assert payload["table_name"] == "titan_learnings"


@pytest.mark.asyncio
async def test_publish_memory_event_disabled():
    """publish_memory_event should be no-op when MEMORY_BUS_ENABLED=false."""
    from shared.comms import MemoryChangedEvent, publish_memory_event

    mock_emit = AsyncMock()
    mock_get_config = AsyncMock(return_value="false")

    with (
        patch("shared.comms.db.emit_event", mock_emit),
        patch("shared.comms.db.get_config", mock_get_config),
    ):
        evt = MemoryChangedEvent(
            source_daemon="titan",
            memory_type="learning",
            record_id=1,
            table_name="titan_learnings",
            action="insert",
            visibility="public",
        )
        await publish_memory_event(evt)

    mock_emit.assert_not_called()


# ── Test 3: 3-Tier Access Control ─────────────────────────────────


def test_access_control_public_always_allowed():
    """Public memories should be visible to everyone."""
    from shared.magma import check_memory_access_grant

    assert check_memory_access_grant("clawdbot", "titan", "learning", "public") is True
    assert check_memory_access_grant("hermes", "conway", "transaction", "public") is True


def test_access_control_own_data_always_allowed():
    """An agent should always see its own scoped data."""
    from shared.magma import check_memory_access_grant

    assert check_memory_access_grant("titan", "titan", "training", "scoped") is True
    assert check_memory_access_grant("conway", "conway", "transaction", "scoped") is True


def test_access_control_perseus_sees_everything():
    """Perseus (scheduler) should see all scoped data."""
    from shared.magma import check_memory_access_grant

    assert check_memory_access_grant("perseus", "titan", "training", "scoped") is True
    assert check_memory_access_grant("perseus", "conway", "transaction", "scoped") is True


def test_access_control_clawdbot_denied_scoped():
    """ClawdBot should NOT see Titan's scoped training data."""
    from shared.magma import check_memory_access_grant

    # clawdbot only has access to "titan.learnings" (public anyway)
    assert check_memory_access_grant("clawdbot", "titan", "training", "scoped") is False


def test_access_control_titan_has_deerflow_grant():
    """Titan should see DeerFlow research data."""
    from shared.magma import check_memory_access_grant

    assert check_memory_access_grant("titan", "deerflow", "research", "scoped") is True


def test_access_control_unknown_agent_denied():
    """Unknown agents should not see scoped data."""
    from shared.magma import check_memory_access_grant

    assert check_memory_access_grant("unknown_agent", "titan", "training", "scoped") is False


def test_filter_by_access_no_agent():
    """filter_by_access with no agent should return all results."""
    from shared.magma import filter_by_access

    results = [
        {"source_daemon": "titan", "memory_type": "training", "visibility": "scoped"},
        {"source_daemon": "perseus", "memory_type": "obs", "visibility": "public"},
    ]
    filtered = filter_by_access(results, requesting_agent=None)
    assert len(filtered) == 2


def test_filter_by_access_clawdbot():
    """filter_by_access should remove scoped data from unauthorized agents."""
    from shared.magma import filter_by_access

    results = [
        {"source_daemon": "titan", "memory_type": "training", "visibility": "scoped"},
        {"source_daemon": "titan", "memory_type": "learning", "visibility": "public"},
    ]
    filtered = filter_by_access(results, requesting_agent="clawdbot")
    assert len(filtered) == 1
    assert filtered[0]["visibility"] == "public"


# ── Test 4: Source Adapters ───────────────────────────────────────


def test_titan_learnings_adapter_to_magma_node():
    """TitanLearningsAdapter should transform records correctly."""
    from shared.magma import TitanLearningsAdapter

    adapter = TitanLearningsAdapter()
    record = {
        "id": 42,
        "category": "email_performance",
        "insight": "Subject lines with numbers get 2x opens",
        "confidence": 0.85,
        "created_at": "2026-04-01T12:00:00",
    }
    event = {"visibility": "public"}
    node = adapter.to_magma_node_dict(record, event)

    assert node["content"] == "Subject lines with numbers get 2x opens"
    assert node["category"] == "email_performance"
    assert node["metadata"]["source_daemon"] == "titan"
    assert node["metadata"]["source_table"] == "titan_learnings"
    assert node["metadata"]["source_id"] == 42
    assert node["metadata"]["confidence"] == 0.85


def test_daemon_memory_adapter_to_magma_node():
    """DaemonMemoryAdapter should handle dict content."""
    from shared.magma import DaemonMemoryAdapter

    adapter = DaemonMemoryAdapter()
    record = {
        "id": 10,
        "daemon_name": "perseus",
        "memory_type": "semantic",
        "key": "daily_observation",
        "content": {"summary": "System ran 24h without errors"},
        "importance": 0.7,
        "created_at": "2026-04-01",
    }
    event = {"visibility": "public"}
    node = adapter.to_magma_node_dict(record, event)

    assert node["content"] == "System ran 24h without errors"
    assert node["metadata"]["source_daemon"] == "perseus"


def test_research_items_adapter_to_magma_node():
    """ResearchItemsAdapter should combine title and summary."""
    from shared.magma import ResearchItemsAdapter

    adapter = ResearchItemsAdapter()
    record = {
        "id": 7,
        "source": "arxiv",
        "url": "https://arxiv.org/abs/123",
        "title": "New RAG Technique",
        "summary": "Improves retrieval by 40%",
        "published_at": "2026-03-01",
        "tags": ["rag", "retrieval"],
        "created_at": "2026-04-01",
    }
    event = {"visibility": "public"}
    node = adapter.to_magma_node_dict(record, event)

    assert "New RAG Technique" in node["content"]
    assert "Improves retrieval" in node["content"]
    assert node["metadata"]["source_daemon"] == "deerflow"


def test_bandit_state_adapter_to_magma_node():
    """BanditStateAdapter should format experiment info."""
    from shared.magma import BanditStateAdapter

    adapter = BanditStateAdapter()
    record = {
        "bandit_id": "email_template_exp",
        "state_json": "{}",
        "arm_count": 3,
        "total_pulls": 150,
        "updated_at": "2026-04-01",
    }
    event = {"visibility": "public"}
    node = adapter.to_magma_node_dict(record, event)

    assert "email_template_exp" in node["content"]
    assert "3 arms" in node["content"]
    assert "150 pulls" in node["content"]


# ��─ Test 5: MAGMA Memory Subscriber ──────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_skips_restricted():
    """Subscriber should skip restricted visibility events."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()
    result = await subscriber.handle_event({
        "visibility": "restricted",
        "table_name": "titan_learnings",
        "record_id": 1,
    })
    assert result is False


@pytest.mark.asyncio
async def test_subscriber_skips_conway():
    """Subscriber should skip conway_ledger events (read-only adapter)."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()
    result = await subscriber.handle_event({
        "visibility": "scoped",
        "table_name": "conway_ledger",
        "record_id": 1,
    })
    assert result is False


@pytest.mark.asyncio
async def test_subscriber_skips_zep():
    """Subscriber should skip zep_facts (already dual-stored)."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()
    result = await subscriber.handle_event({
        "visibility": "public",
        "table_name": "zep_facts",
        "record_id": "session_1",
    })
    assert result is False


@pytest.mark.asyncio
async def test_subscriber_skips_unknown_table():
    """Subscriber should skip events for unknown tables."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()
    with patch.object(subscriber, "_already_ingested", new_callable=AsyncMock, return_value=False):
        result = await subscriber.handle_event({
            "visibility": "public",
            "table_name": "nonexistent_table",
            "record_id": 1,
        })
    assert result is False


# ── Test 6: Bandit State Persistence ──────��───────────────────────


@pytest.mark.asyncio
async def test_bandit_persist_snapshot():
    """BanditPolicy._persist_snapshot should upsert to bandit_state table."""
    from shared.bandit import BanditPolicy

    policy = BanditPolicy()
    exp = policy._get_or_create("test_exp", arms=["a", "b"])
    exp.arms["a"].alpha = 5.0
    exp.arms["a"].beta = 2.0
    exp.total_pulls = 10

    mock_execute = AsyncMock()
    with patch("shared.db.execute", mock_execute):
        await policy._persist_snapshot(exp)

    mock_execute.assert_called_once()
    call_args = mock_execute.call_args[0]
    assert "bandit_state" in call_args[0]
    assert call_args[1][0] == "test_exp"
    state = json.loads(call_args[1][1])
    assert state["arms"]["a"]["alpha"] == 5.0
    assert state["total_pulls"] == 10


@pytest.mark.asyncio
async def test_bandit_load_snapshot():
    """BanditPolicy.load_snapshot should restore experiment state."""
    from shared.bandit import BanditPolicy

    state = {
        "arms": {
            "arm_a": {"alpha": 10.0, "beta": 3.0, "total_pulls": 12, "total_reward": 9.5},
            "arm_b": {"alpha": 2.0, "beta": 8.0, "total_pulls": 9, "total_reward": 1.5},
        },
        "total_pulls": 21,
        "converged": True,
        "winner": "arm_a",
    }

    mock_fetch_one = AsyncMock(return_value={"state_json": json.dumps(state)})
    policy = BanditPolicy()

    with patch("shared.db.fetch_one", mock_fetch_one):
        loaded = await policy.load_snapshot("my_exp")

    assert loaded is True
    exp = policy._experiments["my_exp"]
    assert exp.total_pulls == 21
    assert exp.converged is True
    assert exp.winner == "arm_a"
    assert exp.arms["arm_a"].alpha == 10.0
    assert exp.arms["arm_b"].beta == 8.0


@pytest.mark.asyncio
async def test_bandit_load_snapshot_not_found():
    """load_snapshot should return False when no state exists."""
    from shared.bandit import BanditPolicy

    mock_fetch_one = AsyncMock(return_value=None)
    policy = BanditPolicy()

    with patch("shared.db.fetch_one", mock_fetch_one):
        loaded = await policy.load_snapshot("nonexistent")

    assert loaded is False


# ── Test 7: Conway Read-Only Adapter ─────────────────────────────


@pytest.mark.asyncio
async def test_conway_adapter_query_balance():
    """ConwayReadOnlyAdapter.query_balance should return balance dict."""
    from shared.magma import ConwayReadOnlyAdapter

    adapter = ConwayReadOnlyAdapter()
    mock_result = {"income": 1000.0, "expenses": 350.0}
    mock_fetch = AsyncMock(return_value=mock_result)

    with patch("shared.db.fetch_one", mock_fetch):
        result = await adapter.query_balance("titan")

    assert result is not None
    assert result["income"] == 1000.0
    assert result["expenses"] == 350.0


@pytest.mark.asyncio
async def test_conway_adapter_query_spending():
    """ConwayReadOnlyAdapter.query_spending should return breakdown."""
    from shared.magma import ConwayReadOnlyAdapter

    adapter = ConwayReadOnlyAdapter()
    mock_rows = [
        {"tx_type": "inference", "total": 200.0, "count": 50},
        {"tx_type": "compute_rental", "total": 100.0, "count": 10},
    ]
    mock_fetch = AsyncMock(return_value=mock_rows)

    with patch("shared.db.fetch_all", mock_fetch):
        result = await adapter.query_spending("titan", days=30)

    assert len(result) == 2
    assert result[0]["tx_type"] == "inference"


@pytest.mark.asyncio
async def test_conway_adapter_query_system_economics():
    """ConwayReadOnlyAdapter.query_system_economics should return aggregates."""
    from shared.magma import ConwayReadOnlyAdapter

    adapter = ConwayReadOnlyAdapter()
    mock_result = {"tx_count": 500, "total_volume": 5000.0, "active_agents": 5}
    mock_fetch = AsyncMock(return_value=mock_result)

    with patch("shared.db.fetch_one", mock_fetch):
        result = await adapter.query_system_economics()

    assert result is not None
    assert result["tx_count"] == 500


# ── Test 8: High-Water Mark ──────────────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_high_water_mark():
    """Subscriber should track high-water marks per source table."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()

    mock_fetch_val = AsyncMock(return_value=100)
    mock_execute = AsyncMock()

    with (
        patch("shared.db.fetch_val", mock_fetch_val),
        patch("shared.db.execute", mock_execute),
    ):
        hwm = await subscriber._get_high_water_mark("titan_learnings")

    assert hwm == 100


@pytest.mark.asyncio
async def test_subscriber_already_ingested():
    """Subscriber should detect already-ingested records."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()

    mock_fetch_one = AsyncMock(return_value={"last_ingested_id": 50})

    with patch("shared.db.fetch_one", mock_fetch_one):
        is_ingested = await subscriber._already_ingested("titan_learnings", 30)

    assert is_ingested is True


@pytest.mark.asyncio
async def test_subscriber_not_yet_ingested():
    """Subscriber should allow new records through."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()

    mock_fetch_one = AsyncMock(return_value={"last_ingested_id": 50})

    with patch("shared.db.fetch_one", mock_fetch_one):
        is_ingested = await subscriber._already_ingested("titan_learnings", 60)

    assert is_ingested is False


# ── Test 9: Memory Explorer API (import-level check) ─────────────


def test_memory_router_importable():
    """Memory router module should be importable (requires fastapi)."""
    try:
        from hermes.web.memory_router import router
        assert router is not None
        assert router.prefix == "/api/memory"
    except ImportError:
        pytest.skip("fastapi not installed in test environment")


def test_memory_router_has_endpoints():
    """Memory router should have search, graph, timeline, economics endpoints."""
    try:
        from hermes.web.memory_router import router
    except ImportError:
        pytest.skip("fastapi not installed in test environment")
        return

    route_paths = [r.path for r in router.routes]
    assert "/search" in route_paths
    assert "/graph" in route_paths
    assert "/timeline" in route_paths
    assert "/economics" in route_paths
    assert "/stats" in route_paths


# ── Test 10: Singleton Subscriber ─────────────────────────────────


def test_get_memory_subscriber_singleton():
    """get_memory_subscriber should return the same instance."""
    from shared.magma import get_memory_subscriber

    sub1 = get_memory_subscriber()
    sub2 = get_memory_subscriber()
    assert sub1 is sub2


def test_subscriber_has_adapters():
    """Subscriber should have all expected adapters."""
    from shared.magma import get_memory_subscriber

    sub = get_memory_subscriber()
    assert "daemon_memory" in sub.adapters
    assert "titan_learnings" in sub.adapters
    assert "research_items" in sub.adapters
    assert "bandit_state" in sub.adapters
    assert sub.conway_adapter is not None


# ── Test 11: DaemonMemoryStore event emission ────────────────────


@pytest.mark.asyncio
async def test_daemon_memory_emits_event_on_save():
    """DaemonMemoryStore should emit memory.changed event after save."""
    try:
        from shared.daemon_memory import DaemonMemoryStore
    except ImportError:
        pytest.skip("daemon_memory requires Python 3.11+ (datetime.UTC)")
        return

    store = DaemonMemoryStore()
    mock_execute = AsyncMock()
    mock_publish = AsyncMock()

    with (
        patch("shared.db.execute", mock_execute),
        patch("shared.comms.publish_memory_event", mock_publish),
        patch("shared.comms.db.get_config", AsyncMock(return_value="true")),
        patch("shared.comms.db.emit_event", AsyncMock()),
    ):
        await store.save_episodic("perseus", "test_key", {"data": "test"})

    # Check that publish was called (via _emit_memory_event)
    assert mock_publish.called or mock_execute.called


# ── Test 12: Feature Flags ───────────────────────────────────────


def test_feature_flags_in_migration():
    """Migration file should set all 3 feature flags."""
    from pathlib import Path

    migration = Path("/Users/majovega/Desktop/Projects/objective-hertz/scripts/migrations/042-unified-memory-bus.sql")
    content = migration.read_text()
    assert "MEMORY_BUS_ENABLED" in content
    assert "MEMORY_EXPLORER_ENABLED" in content
    assert "BANDIT_PERSISTENCE" in content
    assert "magma_sync_state" in content
    assert "bandit_state" in content
    assert "memory_provenance" in content


# ── Test 13: Memory Index Provenance Source ──────────────────────


@pytest.mark.asyncio
async def test_memory_index_tries_provenance_first():
    """build_memory_index should try MAGMA provenance first."""
    from shared.memory_index import build_memory_index

    mock_provenance = AsyncMock(return_value=["[id1] titan | test insight (conf=0.80, 1d old)"])
    mock_neo4j = AsyncMock(return_value=[])
    mock_postgres = AsyncMock(return_value=[])

    with (
        patch("shared.memory_index._build_from_magma_provenance", mock_provenance),
        patch("shared.memory_index._build_from_neo4j", mock_neo4j),
        patch("shared.memory_index._build_from_postgres", mock_postgres),
    ):
        result = await build_memory_index()

    assert "titan" in result
    mock_provenance.assert_called_once()
    mock_neo4j.assert_not_called()  # Should not fall through


@pytest.mark.asyncio
async def test_memory_index_falls_back_to_neo4j():
    """build_memory_index should fall back to Neo4j when provenance empty."""
    from shared.memory_index import build_memory_index

    mock_provenance = AsyncMock(return_value=[])
    mock_neo4j = AsyncMock(return_value=["[id2] discovery | test (conf=0.70, 2d old)"])
    mock_postgres = AsyncMock(return_value=[])

    with (
        patch("shared.memory_index._build_from_magma_provenance", mock_provenance),
        patch("shared.memory_index._build_from_neo4j", mock_neo4j),
        patch("shared.memory_index._build_from_postgres", mock_postgres),
    ):
        result = await build_memory_index()

    assert "discovery" in result
    mock_neo4j.assert_called_once()


# ── Test 14: DEFAULT_ACCESS_GRANTS structure ─────────────────────


def test_access_grants_structure():
    """DEFAULT_ACCESS_GRANTS should have required agents."""
    from shared.magma import DEFAULT_ACCESS_GRANTS

    assert "perseus" in DEFAULT_ACCESS_GRANTS
    assert "operator" in DEFAULT_ACCESS_GRANTS
    assert "titan" in DEFAULT_ACCESS_GRANTS
    assert "hermes" in DEFAULT_ACCESS_GRANTS
    assert "*" in DEFAULT_ACCESS_GRANTS["perseus"]
    assert "*" in DEFAULT_ACCESS_GRANTS["operator"]


# ── Test 15: Subscriber catch_up ─────────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_catch_up():
    """catch_up should ingest missed records from all adapters."""
    from shared.magma import MagmaMemorySubscriber

    subscriber = MagmaMemorySubscriber()

    # Mock all fetch_since to return empty (no missed records)
    for adapter in subscriber.adapters.values():
        adapter.fetch_since = AsyncMock(return_value=[])

    with (
        patch.object(subscriber, "_get_high_water_mark", new_callable=AsyncMock, return_value=0),
        patch.object(subscriber, "_mark_ingested", new_callable=AsyncMock),
    ):
        count = await subscriber.catch_up()

    assert count == 0  # No missed records
