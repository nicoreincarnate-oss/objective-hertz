"""Tests for Task 23-01: Unified LLM client factory (Anatomy Integration Phase 23).

Validates:
- Factory creates LLMClient by default (flag off)
- Factory creates unified wrapper when flag on
- Both backends satisfy LLMProtocol
- Backward-compatible import: `from shared.llm_client import llm`
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing shared.llm_client
# ---------------------------------------------------------------------------

# psycopg mock
if "psycopg" not in sys.modules:
    _fake_psycopg = types.ModuleType("psycopg")
    _fake_psycopg_rows = types.ModuleType("psycopg.rows")
    _fake_psycopg_rows.dict_row = MagicMock()
    _fake_psycopg.rows = _fake_psycopg_rows
    _fake_psycopg.Error = type("Error", (Exception,), {})
    sys.modules["psycopg"] = _fake_psycopg
    sys.modules["psycopg.rows"] = _fake_psycopg_rows

if "psycopg_pool" not in sys.modules:
    _fake_pool = types.ModuleType("psycopg_pool")
    _fake_pool.AsyncConnectionPool = MagicMock
    sys.modules["psycopg_pool"] = _fake_pool

# httpx mock
_fake_httpx = types.ModuleType("httpx")
_fake_httpx.AsyncClient = MagicMock
_fake_httpx.Timeout = MagicMock
_fake_httpx.HTTPError = type("HTTPError", (Exception,), {})
sys.modules.setdefault("httpx", _fake_httpx)

# shared.db mock
_fake_db = types.ModuleType("shared.db")
for attr in ("execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool",
             "insert_task", "transaction", "emit_event"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.get_config = AsyncMock(return_value=None)
sys.modules["shared.db"] = _fake_db

# shared.config mock
_fake_config_mod = types.ModuleType("shared.config")
_fake_config_mod.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test-llm"),
    claude=types.SimpleNamespace(
        api_key="",
        primary_model="claude-sonnet-4-20250514",
        genius_model="claude-opus-4-20250514",
        fast_model="claude-haiku-3-5-20241022",
    ),
    ollama=types.SimpleNamespace(
        host="http://localhost:11434",
        model="llama3.2",
        secondary="llama3.2:1b",
        embed_model="nomic-embed-text",
        kv_cache_type="turbo4",
        flash_attention=True,
    ),
    airllm=types.SimpleNamespace(model=None, enabled=False, compression="4bit",
                                  profiling_mode=False, hf_token="",
                                  layer_shards_path="",
                                  prompt_char_threshold=12000,
                                  preferred_stages="research"),
    ollm=types.SimpleNamespace(enabled=False, model="", prompt_char_threshold=40000,
                                preferred_stages="deep_research"),
    budget=types.SimpleNamespace(monthly_cap=800, alert_threshold=0.80, cloud_gpu_cap=200),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="",
                                  neo4j_password="", mem0_host="", qdrant_host="",
                                  qdrant_collection="", zep_url="", zep_enabled=False),
    observability=types.SimpleNamespace(environment="test", release="0.0.1",
                                         metrics_enabled=False),
)
sys.modules["shared.config"] = _fake_config_mod

# shared.airllm_policy mock
_fake_airllm_policy = types.ModuleType("shared.airllm_policy")
_fake_airllm_policy.choose_heavy_local_backend = MagicMock(return_value="ollama")
_fake_airllm_policy.explain_heavy_local_routing = MagicMock(return_value="ollama")
_fake_airllm_policy.should_route_to_heavy_local = MagicMock(return_value=False)
sys.modules["shared.airllm_policy"] = _fake_airllm_policy

# shared.prompt_builder mock
_fake_prompt_builder = types.ModuleType("shared.prompt_builder")
_fake_prompt_builder.CACHE_BOUNDARY_MARKER = "---CACHE-BOUNDARY---"
_fake_prompt_builder.get_session_latch = MagicMock(return_value=MagicMock(
    peek=MagicMock(return_value=None),
    get=MagicMock(side_effect=lambda key, factory: factory()),
))
sys.modules["shared.prompt_builder"] = _fake_prompt_builder

# shared.middleware mock
_fake_middleware = types.ModuleType("shared.middleware")
_fake_middleware.check_budget_for_llm_call = AsyncMock(return_value="smart")
sys.modules["shared.middleware"] = _fake_middleware

# shared.observability mock
_fake_obs = types.ModuleType("shared.observability")
_fake_obs.record_llm_call = AsyncMock()
_fake_obs._task_id = MagicMock()
_fake_obs._task_id.get = MagicMock(return_value=None)
sys.modules["shared.observability"] = _fake_obs

# shared.cost_events mock
_fake_cost_events = types.ModuleType("shared.cost_events")
_fake_cost_events.CostEvent = MagicMock
_fake_cost_events.emit_cost_event = AsyncMock()
sys.modules["shared.cost_events"] = _fake_cost_events

# shared.agent_dna mock
_fake_dna = types.ModuleType("shared.agent_dna")
_fake_dna.get_dna = MagicMock(return_value=None)
_fake_dna.get_circuit_breaker = MagicMock(return_value=MagicMock(record=MagicMock()))
sys.modules["shared.agent_dna"] = _fake_dna

# shared.learning_extractor mock
_fake_learning = types.ModuleType("shared.learning_extractor")
_fake_learning.maybe_extract_background = MagicMock()
sys.modules["shared.learning_extractor"] = _fake_learning

# shared.memory_index mock
_fake_memory_index = types.ModuleType("shared.memory_index")
_fake_memory_index.get_memory_index = MagicMock(return_value="")
_fake_memory_index._MAX_ENTRIES = 100
sys.modules["shared.memory_index"] = _fake_memory_index

# openjarvis mocks (for CloudEngine wrapper tests)
for mod_path in [
    "openjarvis", "openjarvis.core", "openjarvis.core.registry",
    "openjarvis.core.types", "openjarvis.engine", "openjarvis.engine._base",
    "openjarvis.engine._stubs", "openjarvis.engine.cloud",
]:
    if mod_path not in sys.modules:
        sys.modules[mod_path] = types.ModuleType(mod_path)

# Set up minimal openjarvis.core.types with Message and MessageRole
_oj_types = sys.modules["openjarvis.core.types"]
_oj_types.MessageRole = types.SimpleNamespace(
    SYSTEM="system", USER="user", ASSISTANT="assistant", TOOL="tool",
)
_oj_types.Message = MagicMock


# Set up EngineRegistry as no-op decorator
_oj_registry = sys.modules["openjarvis.core.registry"]
_oj_registry.EngineRegistry = MagicMock()
_oj_registry.EngineRegistry.register = MagicMock(return_value=lambda cls: cls)

# Set up engine stubs
_oj_stubs = sys.modules["openjarvis.engine._stubs"]
_oj_stubs.InferenceEngine = type("InferenceEngine", (), {})
_oj_stubs.ResponseFormat = MagicMock

# Set up engine _base
_oj_base = sys.modules["openjarvis.engine._base"]
_oj_base.EngineConnectionError = type("EngineConnectionError", (Exception,), {})
_oj_base.InferenceEngine = _oj_stubs.InferenceEngine
_oj_base.messages_to_dicts = MagicMock(return_value=[])

# Now import (will use all the mocks above)
# Ensure ANATOMY_UNIFIED_LLM is off for the initial import
os.environ["ANATOMY_UNIFIED_LLM"] = ""
import shared.llm_client  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_llm_client(env_overrides: dict[str, str] | None = None):
    """Reload shared.llm_client with optional env var overrides."""
    env = env_overrides or {}
    with mock.patch.dict(os.environ, env, clear=False):
        importlib.reload(shared.llm_client)
        return shared.llm_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUnifiedLLMFactory:
    """Test suite for the UnifiedLLMFactory and _create_llm_client."""

    def test_factory_creates_llm_client_by_default(self):
        """When ANATOMY_UNIFIED_LLM is not set, factory returns LLMClient."""
        mod = _reload_llm_client({"ANATOMY_UNIFIED_LLM": ""})
        try:
            assert isinstance(mod.llm, mod.LLMClient), (
                f"Expected LLMClient, got {type(mod.llm).__name__}"
            )
        finally:
            _reload_llm_client({"ANATOMY_UNIFIED_LLM": ""})

    def test_factory_creates_llm_client_when_flag_false(self):
        """When ANATOMY_UNIFIED_LLM=false, factory returns LLMClient."""
        mod = _reload_llm_client({"ANATOMY_UNIFIED_LLM": "false"})
        try:
            assert isinstance(mod.llm, mod.LLMClient), (
                f"Expected LLMClient, got {type(mod.llm).__name__}"
            )
        finally:
            _reload_llm_client({"ANATOMY_UNIFIED_LLM": ""})

    def test_factory_creates_unified_when_flag_on(self):
        """When ANATOMY_UNIFIED_LLM=true + USE_OJ_ENGINE=false, factory still returns LLMClient."""
        mod = _reload_llm_client({
            "ANATOMY_UNIFIED_LLM": "true",
            "USE_OJ_ENGINE": "false",
        })
        try:
            assert isinstance(mod.llm, mod.LLMClient), (
                f"Expected LLMClient when USE_OJ_ENGINE=false, got {type(mod.llm).__name__}"
            )
        finally:
            _reload_llm_client({"ANATOMY_UNIFIED_LLM": "", "USE_OJ_ENGINE": ""})

    def test_factory_creates_cloud_wrapper_when_oj_engine(self):
        """When ANATOMY_UNIFIED_LLM=true + USE_OJ_ENGINE=true, returns CloudEngineWrapper."""
        mod = _reload_llm_client({
            "ANATOMY_UNIFIED_LLM": "true",
            "USE_OJ_ENGINE": "true",
        })
        try:
            assert isinstance(mod.llm, mod._CloudEngineWrapper), (
                f"Expected _CloudEngineWrapper, got {type(mod.llm).__name__}"
            )
        finally:
            _reload_llm_client({"ANATOMY_UNIFIED_LLM": "", "USE_OJ_ENGINE": ""})

    def test_unified_has_generate(self):
        """Both LLMClient and _CloudEngineWrapper have generate() method."""
        from shared.llm_client import LLMClient, _CloudEngineWrapper

        assert hasattr(LLMClient, "generate"), "LLMClient missing generate()"
        assert hasattr(_CloudEngineWrapper, "generate"), "_CloudEngineWrapper missing generate()"
        assert callable(getattr(LLMClient, "generate")), "LLMClient.generate not callable"
        assert callable(getattr(_CloudEngineWrapper, "generate")), "_CloudEngineWrapper.generate not callable"

    def test_unified_has_generate_with_images(self):
        """Both LLMClient and _CloudEngineWrapper have generate_with_images() method."""
        from shared.llm_client import LLMClient, _CloudEngineWrapper

        assert hasattr(LLMClient, "generate_with_images"), (
            "LLMClient missing generate_with_images()"
        )
        assert hasattr(_CloudEngineWrapper, "generate_with_images"), (
            "_CloudEngineWrapper missing generate_with_images()"
        )

    def test_protocol_compliance(self):
        """Both LLMClient and _CloudEngineWrapper satisfy LLMProtocol."""
        from shared.llm_client import LLMClient, LLMProtocol, _CloudEngineWrapper

        client = LLMClient()
        wrapper = _CloudEngineWrapper()

        assert isinstance(client, LLMProtocol), (
            "LLMClient does not satisfy LLMProtocol"
        )
        assert isinstance(wrapper, LLMProtocol), (
            "_CloudEngineWrapper does not satisfy LLMProtocol"
        )

    def test_backward_compat_import(self):
        """The import `from shared.llm_client import llm` still works."""
        from shared.llm_client import llm

        assert llm is not None, "llm singleton is None"
        assert hasattr(llm, "generate"), "llm singleton missing generate()"
        assert hasattr(llm, "generate_with_images"), "llm singleton missing generate_with_images()"

    def test_factory_function_exists(self):
        """_create_llm_client function is importable."""
        from shared.llm_client import _create_llm_client

        assert callable(_create_llm_client)

    def test_unified_factory_class_exists(self):
        """UnifiedLLMFactory class is importable and has create() method."""
        from shared.llm_client import UnifiedLLMFactory

        assert hasattr(UnifiedLLMFactory, "create")
        assert callable(UnifiedLLMFactory.create)

    def test_cloud_wrapper_lazy_engine_init(self):
        """_CloudEngineWrapper does not import CloudEngine at __init__ time."""
        from shared.llm_client import _CloudEngineWrapper

        wrapper = _CloudEngineWrapper()
        assert wrapper._engine is None, (
            "CloudEngine should not be instantiated at __init__ time"
        )

    def test_cloud_wrapper_has_close(self):
        """_CloudEngineWrapper has a close() method for resource cleanup."""
        from shared.llm_client import _CloudEngineWrapper

        wrapper = _CloudEngineWrapper()
        assert hasattr(wrapper, "close"), "_CloudEngineWrapper missing close()"

    def test_protocol_is_runtime_checkable(self):
        """LLMProtocol is decorated with @runtime_checkable."""
        from shared.llm_client import LLMProtocol

        # A runtime_checkable Protocol supports isinstance() checks.
        # If it weren't, isinstance(obj, LLMProtocol) would raise TypeError.
        wrapper = shared.llm_client._CloudEngineWrapper()
        try:
            result = isinstance(wrapper, LLMProtocol)
            # If we got here without TypeError, it's runtime_checkable
            assert isinstance(result, bool)
        except TypeError:
            pytest.fail("LLMProtocol is not runtime_checkable")
