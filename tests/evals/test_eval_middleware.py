"""Behavioral eval: middleware chain MUST execute in Titan daemon.

The middleware chain must not be dead code -- it must be imported AND called
by titan/daemon.py during pipeline execution. Per AEGIS audit requirement.
"""

from __future__ import annotations

import ast

import pytest


def test_titan_daemon_imports_middleware():
    """Verify titan/daemon.py imports shared.middleware at module level or in methods."""
    with open("titan/daemon.py", "r") as f:
        source = f.read()
    tree = ast.parse(source)

    middleware_imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "middleware" in node.module:
                middleware_imported = True
                break
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "middleware" in alias.name:
                    middleware_imported = True
                    break

    assert middleware_imported, (
        "titan/daemon.py MUST import shared.middleware. "
        "Middleware chain is dead code without this import."
    )


def test_titan_daemon_calls_build_chain_or_middleware():
    """Verify titan/daemon.py references middleware chain building."""
    with open("titan/daemon.py", "r") as f:
        source = f.read()

    # Must reference build_chain or run_middleware or check_budget_for_llm_call
    middleware_used = any(
        keyword in source
        for keyword in ["build_chain", "run_middleware", "check_budget_for_llm_call"]
    )

    assert middleware_used, (
        "titan/daemon.py must actually USE the middleware (build_chain, "
        "run_middleware, or check_budget_for_llm_call), not just import it."
    )


def test_llm_client_calls_budget_middleware():
    """Verify shared/llm_client.py delegates to middleware for budget checks."""
    with open("shared/llm_client.py", "r") as f:
        source = f.read()

    assert "check_budget_for_llm_call" in source, (
        "shared/llm_client.py MUST use check_budget_for_llm_call from middleware. "
        "Without this, budget enforcement is bypassed."
    )
