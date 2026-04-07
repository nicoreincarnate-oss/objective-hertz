"""Phase 42 — MAGMA runtime smoke tests.

These tests verify the Phase 42 modules (built in parallel by agents A1-A7)
import cleanly and expose the expected API surface. They are tolerant of
modules not yet existing (they `pytest.importorskip` so missing modules
become test skips, not failures).

Run: pytest tests/test_phase42_magma_runtime.py -v
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

# ── Phase 42 module manifest ─────────────────────────────────────────
# (module_name, [expected_attributes])
PHASE42_MODULES: list[tuple[str, list[str]]] = [
    ("scripts.bootstrap_magma", ["main"]),
    ("shared.magma_writer", ["write_event_memory", "ensure_daemon_node", "get_recent_nodes_for_daemon"]),
    ("shared.consolidator", ["consolidate_recent_events"]),
    ("shared.procedural_extractor", []),  # signature checked dynamically
    ("shared.edge_inference", []),
    ("shared.magma_edge_types", ["EdgeType"]),
    ("shared.operator_memory_hook", []),
]


def _try_import(module_name: str):
    """Import a module if it exists, otherwise return None."""
    try:
        return __import__(module_name, fromlist=["*"])
    except ImportError:
        return None


# ── Individual module tests ──────────────────────────────────────────


def test_bootstrap_magma_module_imports():
    mod = pytest.importorskip("scripts.bootstrap_magma")
    assert hasattr(mod, "main"), "bootstrap_magma must expose main()"
    assert callable(mod.main), "bootstrap_magma.main must be callable"


def test_magma_writer_module_imports():
    mod = pytest.importorskip("shared.magma_writer")
    for fn_name in ("write_event_memory", "ensure_daemon_node", "get_recent_nodes_for_daemon"):
        assert hasattr(mod, fn_name), f"shared.magma_writer must expose {fn_name}"
        assert callable(getattr(mod, fn_name)), f"{fn_name} must be callable"


def test_consolidator_module_imports():
    mod = pytest.importorskip("shared.consolidator")
    assert hasattr(mod, "consolidate_recent_events"), (
        "shared.consolidator must expose consolidate_recent_events()"
    )
    assert callable(mod.consolidate_recent_events)


def test_procedural_extractor_imports():
    mod = pytest.importorskip("shared.procedural_extractor")
    # Expect at least one extraction function
    candidates = [
        name for name in dir(mod)
        if not name.startswith("_") and callable(getattr(mod, name, None))
    ]
    assert len(candidates) >= 1, (
        "shared.procedural_extractor must expose at least one extractor function"
    )


def test_edge_inference_imports():
    mod = pytest.importorskip("shared.edge_inference")
    candidates = [
        name for name in dir(mod)
        if not name.startswith("_") and callable(getattr(mod, name, None))
    ]
    assert len(candidates) >= 1, (
        "shared.edge_inference must expose at least one inference function"
    )


def test_magma_edge_types_imports():
    mod = pytest.importorskip("shared.magma_edge_types")
    assert hasattr(mod, "EdgeType"), "shared.magma_edge_types must expose EdgeType enum"
    edge_type = mod.EdgeType
    # Enum should have at least 8 members per spec
    members: list[Any] = list(edge_type)
    assert len(members) >= 8, (
        f"EdgeType enum should have ≥8 values (got {len(members)}: {members})"
    )


def test_operator_hook_imports():
    mod = pytest.importorskip("shared.operator_memory_hook")
    candidates = [
        name for name in dir(mod)
        if not name.startswith("_") and callable(getattr(mod, name, None))
    ]
    assert len(candidates) >= 1, (
        "shared.operator_memory_hook must expose at least one hook function"
    )


# ── Meta test: report which modules are present ──────────────────────


def test_all_phase42_modules_listed(capsys):
    """Meta-test: print Phase 42 module presence report."""
    present: list[str] = []
    missing: list[str] = []
    for mod_name, _ in PHASE42_MODULES:
        if _try_import(mod_name) is not None:
            present.append(mod_name)
        else:
            missing.append(mod_name)

    print("\n── Phase 42 Module Presence Report ──")
    print(f"Present ({len(present)}/{len(PHASE42_MODULES)}):")
    for m in present:
        print(f"  ✅ {m}")
    if missing:
        print(f"Missing ({len(missing)}):")
        for m in missing:
            print(f"  ⚠  {m}")
    else:
        print("All Phase 42 modules present.")

    # This test never fails — it's informational
    assert True


# ── Signature sanity checks (only run if modules exist) ──────────────


def test_write_event_memory_signature():
    mod = pytest.importorskip("shared.magma_writer")
    fn = getattr(mod, "write_event_memory", None)
    if fn is None:
        pytest.skip("write_event_memory not present yet")
    sig = inspect.signature(fn)
    # Expect at least one parameter (the event payload)
    assert len(sig.parameters) >= 1, (
        f"write_event_memory should accept at least 1 arg (got {sig})"
    )


def test_consolidate_recent_events_signature():
    mod = pytest.importorskip("shared.consolidator")
    fn = getattr(mod, "consolidate_recent_events", None)
    if fn is None:
        pytest.skip("consolidate_recent_events not present yet")
    sig = inspect.signature(fn)
    # Should be callable with no required args (defaults OK) or with a window arg
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    assert len(required) <= 2, (
        f"consolidate_recent_events should have ≤2 required args (got {required})"
    )
