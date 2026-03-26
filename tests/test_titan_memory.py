"""Tests for titan/memory.py — memory + learning system."""

import asyncio
import importlib
import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _setup_fakes():
    """Install fake modules, return (saved, titan_memory_module, fakes dict)."""
    modules_to_fake = [
        "shared.db", "shared.config", "shared.llm_client", "shared.comms",
        "titan.memory",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock(return_value=1)
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock(return_value=None)
    fake_db.fetch_val = AsyncMock(return_value=None)
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()
    fake_db.init_pool = AsyncMock()
    fake_db.close_pool = AsyncMock()
    fake_db.insert_task = AsyncMock(return_value=1)
    fake_db.transaction = AsyncMock()

    fake_config = types.ModuleType("shared.config")
    fake_config.config = types.SimpleNamespace(
        root_dir=Path("/tmp/test"),
        memory=types.SimpleNamespace(
            mem0_host="http://localhost:8888",
            qdrant_host="http://localhost:6333",
            qdrant_collection="test",
            zep_url="http://localhost:8000",
            zep_enabled=False,
            magma_enabled=False,
            neo4j_uri="bolt://localhost:7687",
            neo4j_user="neo4j",
            neo4j_password="test",
        ),
        ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
    )

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"insights": []}'))

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.record_decision = AsyncMock(return_value=1)
    fake_comms.store_learning = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.config"] = fake_config
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.comms"] = fake_comms
    sys.modules.pop("titan.memory", None)

    mem_mod = importlib.import_module("titan.memory")
    fakes = {
        "db": fake_db, "config": fake_config,
        "llm": fake_llm, "comms": fake_comms,
    }
    return saved, mem_mod, fakes


def _restore(saved):
    for mod_name, orig in saved.items():
        if orig is not None:
            sys.modules[mod_name] = orig
        else:
            sys.modules.pop(mod_name, None)


# ── Module imports correctly ──

def test_titan_memory_imports():
    saved, mem_mod, _ = _setup_fakes()
    try:
        assert hasattr(mem_mod, 'store_memory')
        assert hasattr(mem_mod, 'search_memory')
        assert hasattr(mem_mod, 'get_relevant_learnings')
    finally:
        _restore(saved)


# ── store_memory (mocked) ──

def test_store_memory_calls_mem0():
    import respx
    saved, mem_mod, _ = _setup_fakes()
    try:
        with respx.mock:
            respx.post("http://localhost:8888/v1/memories/").respond(200, json={"id": "test"})
            asyncio.run(mem_mod.store_memory("test content", "email", client_id=1))
    finally:
        _restore(saved)


# ── search_memory (mocked) ──

def test_search_memory_returns_list():
    import respx
    saved, mem_mod, _ = _setup_fakes()
    try:
        with respx.mock:
            respx.post("http://localhost:8888/v1/memories/search/").respond(200, json={
                "results": [{"memory": "found this", "metadata": {}}]
            })
            results = asyncio.run(mem_mod.search_memory("test query"))
            assert isinstance(results, list)
            assert len(results) == 1
            assert "found this" in results[0]
    finally:
        _restore(saved)


def test_search_memory_handles_failure():
    import respx
    saved, mem_mod, _ = _setup_fakes()
    try:
        with respx.mock:
            respx.post("http://localhost:8888/v1/memories/search/").respond(500)
            results = asyncio.run(mem_mod.search_memory("test query"))
            assert results == []
    finally:
        _restore(saved)


# ── get_relevant_learnings ──

def test_get_relevant_learnings_returns_string():
    import respx
    saved, mem_mod, fakes = _setup_fakes()
    try:
        fakes["db"].fetch_all = AsyncMock(return_value=[
            {"category": "email", "insight": "Tuesday sends work best"}
        ])
        with respx.mock:
            respx.post("http://localhost:8888/v1/memories/search/").respond(200, json={"results": []})
            result = asyncio.run(mem_mod.get_relevant_learnings("email tips"))
            assert isinstance(result, str)
            assert "Tuesday" in result or "No prior" in result
    finally:
        _restore(saved)


def test_get_relevant_learnings_no_data():
    import respx
    saved, mem_mod, fakes = _setup_fakes()
    try:
        fakes["db"].fetch_all = AsyncMock(return_value=[])
        with respx.mock:
            respx.post("http://localhost:8888/v1/memories/search/").respond(200, json={"results": []})
            result = asyncio.run(mem_mod.get_relevant_learnings("anything"))
            assert isinstance(result, str)
    finally:
        _restore(saved)


# ═══════════════════════════════════════════════════════════════
# Cycle 13 — Finding 1: client-scoped flat-stack retrieval
# ═══════════════════════════════════════════════════════════════

def test_client_scoped_learnings_uses_client_id_column():
    """get_relevant_learnings with client_id must query titan_learnings.client_id column."""
    import respx
    saved, mem_mod, fakes = _setup_fakes()
    try:
        queries_captured = []

        async def capturing_fetch_all(query, params=None):
            queries_captured.append((query, params))
            return [{"category": "email", "insight": "scoped learning"}]

        fakes["config"].config.memory.magma_enabled = False

        with respx.mock:
            respx.post("http://localhost:8888/v1/memories/search/").respond(200, json={"results": []})
            with patch.object(mem_mod, "fetch_all", capturing_fetch_all):
                asyncio.run(mem_mod.get_relevant_learnings("email tips", client_id=42))

        learning_queries = [(q, p) for q, p in queries_captured if "titan_learnings" in q]
        assert len(learning_queries) >= 1, f"Expected titan_learnings query, captured: {[q[:60] for q, _ in queries_captured]}"
        q, p = learning_queries[0]
        assert "client_id" in q, f"Query must filter on client_id column: {q}"
        assert 42 in p, f"Params must include client_id=42: {p}"
    finally:
        _restore(saved)


def test_sentence_attribution_stores_client_id_as_column():
    """Sentence attribution writers must store client_id as a DB column, not buried in JSON."""
    saved, mem_mod, fakes = _setup_fakes()
    try:
        execute_calls = []

        async def capturing_execute(query, params=None):
            execute_calls.append((query, params))

        attribution_json = json.dumps({
            "trigger_sentence": "We built sites for 200+ businesses",
            "trigger_type": "social_proof",
            "effect": "positive",
            "confidence": 0.8,
        })
        fakes["llm"].llm.generate = AsyncMock(return_value=attribution_json)

        with patch.object(mem_mod, "execute", capturing_execute):
            asyncio.run(mem_mod.attribute_reply_cause(
                original_email="Hi there, check our website. We built sites for 200+ businesses.",
                reply_body="I'm interested!",
                outcome="positive",
                client_id=7,
            ))

        inserts = [(q, p) for q, p in execute_calls if "INSERT INTO titan_learnings" in q]
        assert len(inserts) >= 1, f"Expected INSERT INTO titan_learnings, got: {[q[:60] for q, _ in execute_calls]}"
        q, p = inserts[0]
        assert "client_id" in q, f"INSERT must include client_id column: {q}"
        json_param = p[0]
        assert '"client_id"' not in json_param, f"client_id should not be in JSON insight: {json_param}"
    finally:
        _restore(saved)


# ═══════════════════════════════════════════════════════════════
# Cycle 13 — Finding 2: Zep backfill after expiry filtering
# ═══════════════════════════════════════════════════════════════

def test_zep_temporal_backfill_after_expiry_filtering():
    """System backfill must happen AFTER expired client results are removed."""
    import respx

    saved, mem_mod, fakes = _setup_fakes()
    try:
        past = (datetime.now() - timedelta(days=30)).isoformat()
        future = (datetime.now() + timedelta(days=30)).isoformat()

        fakes["config"].config.memory.zep_enabled = True

        client_results = [
            {"content": "expired fact", "metadata": {"valid_until": past}},
            {"content": "expired fact 2", "metadata": {"valid_until": past}},
            {"content": "expired fact 3", "metadata": {"valid_until": past}},
        ]
        system_results = [
            {"content": "fresh system fact", "metadata": {"valid_until": future}},
        ]

        call_log = []

        with respx.mock:
            def route_handler(request):
                import httpx as _httpx
                url = str(request.url)
                call_log.append(url)
                if "client_42" in url:
                    return _httpx.Response(200, json={"results": client_results})
                elif "titan" in url:
                    return _httpx.Response(200, json={"results": system_results})
                return _httpx.Response(200, json={"results": []})

            respx.post("http://localhost:8000/api/v2/memory/client_42/search").mock(side_effect=route_handler)
            respx.post("http://localhost:8000/api/v2/memory/titan/search").mock(side_effect=route_handler)

            facts = asyncio.run(mem_mod.search_temporal_facts("test query", limit=3, client_id=42))

        titan_calls = [u for u in call_log if "titan" in u]
        assert len(titan_calls) >= 1, f"System backfill should have been triggered: {call_log}"

        contents = [f["content"] for f in facts]
        assert "fresh system fact" in contents, f"Expected fresh system fact, got: {contents}"
        assert "expired fact" not in contents
    finally:
        _restore(saved)


# ═══════════════════════════════════════════════════════════════
# Cycle 13 — Finding 3: consolidation scope
# ═══════════════════════════════════════════════════════════════

def test_graphrag_consolidation_includes_client_namespaces():
    """graphrag_consolidation must consolidate client namespaces, not just 'titan'."""
    import respx

    saved, mem_mod, fakes = _setup_fakes()
    try:
        async def mock_fetch_all(query, params=None):
            if "clients" in query:
                return [{"id": 1}, {"id": 2}]
            return []

        fakes["db"].emit_event = AsyncMock()

        searched_user_ids = []

        with respx.mock:
            def mem0_handler(request):
                import httpx as _httpx
                body = json.loads(request.content)
                uid = body.get("user_id", "")
                searched_user_ids.append(uid)
                return _httpx.Response(200, json={"results": [{"memory": "x", "metadata": {}}] * 3})

            respx.post("http://localhost:8888/v1/memories/search/").mock(side_effect=mem0_handler)

            with patch.object(mem_mod, "fetch_all", mock_fetch_all):
                asyncio.run(mem_mod.graphrag_consolidation())

        assert "titan" in searched_user_ids, f"Must search titan namespace: {searched_user_ids}"
        assert "client:1" in searched_user_ids, f"Must search client:1 namespace: {searched_user_ids}"
        assert "client:2" in searched_user_ids, f"Must search client:2 namespace: {searched_user_ids}"
    finally:
        _restore(saved)
