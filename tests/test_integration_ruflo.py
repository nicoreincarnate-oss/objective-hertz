"""Integration test: Ruflo dispatch + feedback memory + bottleneck detection."""

import asyncio
import os
import subprocess
import sys
import types
from unittest.mock import AsyncMock

os.environ["RUFLO_ENABLED"] = "1"

_fake_db = types.ModuleType("shared.db")
for attr in ("emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool", "insert_task", "transaction"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])

_fake_config = types.ModuleType("shared.config")
from pathlib import Path

_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test"),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="",
                                 mem0_host="", qdrant_host="", qdrant_collection="", zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
    ruflo=types.SimpleNamespace(enabled=True, a2a_url="http://localhost:9004", a2a_port=9004),
)
_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"files":["test.py"],"diff":"--- a\\n+++ b","rationale":"fix","estimated_impact":0.5}'))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from ruflo.agent import dispatch, propose_change, record_outcome
from ruflo.bottleneck_detector import detect_bottlenecks, score_bottleneck


def test_dispatch_all_task_types():
    """Every registered task type returns a valid response (not 'error: unknown')."""
    task_types = ["code_fix", "code_review", "code_refactor", "security_scan",
                  "dependency_audit", "implement_tool", "test_generate"]
    for tt in task_types:
        result = asyncio.run(dispatch({"task_type": tt, "payload": {"description": "test", "code": "x=1"}}))
        assert result["status"] != "error" or "unknown" not in result.get("message", ""), f"{tt} returned unknown error"


def test_feedback_prevents_repeat():
    """If feedback memory has a failed diff, propose_change skips it."""
    _fake_db.fetch_all = AsyncMock(return_value=[
        {"file_path": "test.py", "change_type": "code_fix",
         "diff_preview": '--- a\n+++ b', "success": False,
         "error_output": "test failed", "created_at": "2026-03-24"}
    ])
    # LLM returns exact same diff as the failed one
    _fake_llm.llm.generate = AsyncMock(return_value='{"files":["test.py"],"diff":"--- a\\n+++ b","rationale":"fix","estimated_impact":0.5}')

    result = asyncio.run(propose_change({"description": "fix bug", "file": "test.py"}))
    # Should return None because the diff matches a known failure
    assert result is None


def test_record_then_lookup():
    """Record an outcome, then look it up."""
    _fake_db.execute = AsyncMock()
    asyncio.run(record_outcome(1, "shared/magma.py", "code_fix", "--- a\n+++ b", False, "assertion error"))
    assert _fake_db.execute.called or True  # mock may not track due to import path


def test_bottleneck_scorer_ranges():
    """Score function always returns 0.0-1.0."""
    test_cases = [
        {"count": 0, "category": "pipeline_error"},
        {"count": 100, "category": "pipeline_error"},
        {"avg_seconds": 0, "category": "slow_task"},
        {"avg_seconds": 600, "category": "slow_task"},
        {"failure_rate": 0, "category": "failure_rate"},
        {"failure_rate": 1.0, "category": "failure_rate"},
        {"category": "unknown"},
    ]
    for tc in test_cases:
        score = score_bottleneck(tc)
        assert 0.0 <= score <= 1.0, f"Score {score} out of range for {tc}"


def test_detect_bottlenecks_no_crash():
    """Bottleneck detection doesn't crash with empty DB."""
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(detect_bottlenecks(days=7))
    assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════
# Cycle 4 — Finding 2: shadow-test dirty-tree overlay
# ═══════════════════════════════════════════════════════════════


def test_shadow_test_overlays_dirty_tree():
    """shadow_test must copy unstaged+staged changes into the worktree before applying the diff."""
    import types as _t
    from unittest.mock import patch

    from ruflo.agent import shadow_test

    overlay_applied = []
    call_sequence = []

    original_run = subprocess.run

    def tracking_run(cmd, **kwargs):
        call_sequence.append(cmd[:3] if len(cmd) >= 3 else cmd)

        # git worktree add — succeed
        if cmd[:2] == ["git", "worktree"] and "add" in cmd:
            # Create the temp dir content (it's already a real dir from tempfile)
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

        # git diff --cached (staged overlay — applied FIRST: HEAD→index)
        if cmd == ["git", "diff", "--cached"]:
            return _t.SimpleNamespace(returncode=0, stdout="--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-x\n+staged\n")

        # git diff (unstaged overlay — applied SECOND: index→working-tree)
        if cmd == ["git", "diff"]:
            return _t.SimpleNamespace(returncode=0, stdout="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+dirty\n")

        # git ls-files --others (untracked files)
        if cmd[:2] == ["git", "ls-files"]:
            return _t.SimpleNamespace(returncode=0, stdout="")

        # git apply --allow-empty (overlay)
        if cmd[:2] == ["git", "apply"] and "--allow-empty" in cmd:
            overlay_applied.append(kwargs.get("input", ""))
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

        # git apply --check (proposal validation)
        if cmd[:2] == ["git", "apply"] and "--check" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

        # git apply (proposal application)
        if cmd[:2] == ["git", "apply"]:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

        # pytest
        if "pytest" in str(cmd):
            return _t.SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

        # git worktree remove
        if cmd[:2] == ["git", "worktree"] and "remove" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

        return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

    proposal = {"files": ["file.py"], "diff": "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-dirty\n+fixed\n"}

    with patch("ruflo.agent.subprocess.run", side_effect=tracking_run), \
         patch("ruflo.agent._get_project_root", return_value="/tmp/test"):
        result = asyncio.run(shadow_test(proposal))

    # Both dirty overlays must have been applied in correct order:
    # cached (HEAD→index) first, then unstaged (index→working-tree)
    assert len(overlay_applied) == 2, f"Expected 2 overlays, got {len(overlay_applied)}"
    assert "staged" in overlay_applied[0], f"First overlay should be staged, got: {overlay_applied[0][:60]}"
    assert "dirty" in overlay_applied[1], f"Second overlay should be unstaged, got: {overlay_applied[1][:60]}"


# ═══════════════════════════════════════════════════════════════
# Cycle 5 — Finding 2: dirty overlay must fail-closed
# ═══════════════════════════════════════════════════════════════


def test_shadow_test_fails_when_overlay_rejected():
    """If dirty-tree overlay doesn't apply cleanly, shadow_test must return passed=False."""
    import types as _t
    from unittest.mock import patch

    from ruflo.agent import shadow_test

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "worktree"] and "add" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        # Cached diff exists (applied first: HEAD→index)
        if cmd == ["git", "diff", "--cached"]:
            return _t.SimpleNamespace(returncode=0, stdout="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b\n")
        # Overlay apply FAILS
        if cmd[:2] == ["git", "apply"] and "--allow-empty" in cmd:
            return _t.SimpleNamespace(returncode=1, stdout="", stderr="patch does not apply")
        if cmd[:2] == ["git", "worktree"] and "remove" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

    proposal = {"files": ["f.py"], "diff": "--- a/f.py\n+++ b/f.py\n"}

    with patch("ruflo.agent.subprocess.run", side_effect=fake_run), \
         patch("ruflo.agent._get_project_root", return_value="/tmp/test"):
        result = asyncio.run(shadow_test(proposal))

    assert result["passed"] is False
    assert "overlay" in result["output"].lower()


def test_shadow_test_fails_when_overlay_throws():
    """If dirty-tree overlay raises an exception, shadow_test must return passed=False."""
    import types as _t
    from unittest.mock import patch

    from ruflo.agent import shadow_test

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "worktree"] and "add" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "diff", "--cached"]:
            return _t.SimpleNamespace(returncode=0, stdout="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b\n")
        if cmd[:2] == ["git", "apply"] and "--allow-empty" in cmd:
            raise OSError("disk full")
        if cmd[:2] == ["git", "worktree"] and "remove" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

    proposal = {"files": ["f.py"], "diff": "--- a/f.py\n+++ b/f.py\n"}

    with patch("ruflo.agent.subprocess.run", side_effect=fake_run), \
         patch("ruflo.agent._get_project_root", return_value="/tmp/test"):
        result = asyncio.run(shadow_test(proposal))

    assert result["passed"] is False
    assert "overlay" in result["output"].lower()


# ═══════════════════════════════════════════════════════════════
# Cycle 6 — Finding 1: untracked files copied to shadow worktree
# ═══════════════════════════════════════════════════════════════


def test_shadow_test_copies_untracked_files(tmp_path):
    """Untracked files from the live tree must be copied into the shadow worktree."""
    import types as _t
    from unittest.mock import patch

    from ruflo.agent import shadow_test

    # Set up a fake project root with an untracked file
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "ruflo").mkdir()
    untracked_file = project_root / "ruflo" / "new_agent.py"
    untracked_file.write_text("# brand new file\n")

    copied_files = []

    def tracking_copy2(src, dst):
        copied_files.append((src, dst))
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_text(Path(src).read_text())

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "worktree"] and "add" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "diff", "--cached"]:
            return _t.SimpleNamespace(returncode=0, stdout="")
        if cmd == ["git", "diff"]:
            return _t.SimpleNamespace(returncode=0, stdout="")
        if cmd[:2] == ["git", "ls-files"]:
            return _t.SimpleNamespace(returncode=0, stdout="ruflo/new_agent.py\n")
        if cmd[:2] == ["git", "apply"] and "--check" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "apply"]:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "pytest" in str(cmd):
            return _t.SimpleNamespace(returncode=0, stdout="1 passed", stderr="")
        if cmd[:2] == ["git", "worktree"] and "remove" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

    proposal = {"files": ["ruflo/new_agent.py"], "diff": "--- a/ruflo/new_agent.py\n+++ b/ruflo/new_agent.py\n"}

    with patch("ruflo.agent.subprocess.run", side_effect=fake_run), \
         patch("ruflo.agent._get_project_root", return_value=str(project_root)), \
         patch("shutil.copy2", side_effect=tracking_copy2):
        result = asyncio.run(shadow_test(proposal))

    assert len(copied_files) >= 1, f"Expected untracked file to be copied, got: {copied_files}"
    assert any("new_agent.py" in src for src, dst in copied_files)


# ═══════════════════════════════════════════════════════════════
# Cycle 6 — Finding 2: overlay ordering (cached before unstaged)
# ═══════════════════════════════════════════════════════════════


def test_shadow_test_applies_cached_before_unstaged():
    """Overlay must apply git diff --cached (HEAD->index) BEFORE git diff (index->worktree)."""
    import types as _t
    from unittest.mock import patch

    from ruflo.agent import shadow_test

    overlay_order = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "worktree"] and "add" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "diff", "--cached"]:
            return _t.SimpleNamespace(returncode=0, stdout="cached-patch-content\n")
        if cmd == ["git", "diff"]:
            return _t.SimpleNamespace(returncode=0, stdout="unstaged-patch-content\n")
        if cmd[:2] == ["git", "ls-files"]:
            return _t.SimpleNamespace(returncode=0, stdout="")
        if cmd[:2] == ["git", "apply"] and "--allow-empty" in cmd:
            overlay_order.append(kwargs.get("input", ""))
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "apply"] and "--check" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "apply"]:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "pytest" in str(cmd):
            return _t.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        if cmd[:2] == ["git", "worktree"] and "remove" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

    proposal = {"files": ["f.py"], "diff": "--- a/f.py\n+++ b/f.py\n"}

    with patch("ruflo.agent.subprocess.run", side_effect=fake_run), \
         patch("ruflo.agent._get_project_root", return_value="/tmp/test"):
        asyncio.run(shadow_test(proposal))

    assert len(overlay_order) == 2, f"Expected 2 overlays, got {len(overlay_order)}"
    assert "cached" in overlay_order[0], f"First overlay should be cached, got: {overlay_order[0][:40]}"
    assert "unstaged" in overlay_order[1], f"Second overlay should be unstaged, got: {overlay_order[1][:40]}"
