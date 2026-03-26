"""Tests for the self-audit system."""

import asyncio
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

# Fake modules
_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()
_fake_db.execute = AsyncMock()

_fake_llm_mod = types.ModuleType("shared.llm_client")
_fake_llm_mod.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"findings": []}'))

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.ask_agent = AsyncMock(return_value={"answer": "approve"})
_fake_comms.call_agent_capability = AsyncMock(return_value={"vote": "approve", "reason": "test"})
_fake_comms.request_task_result = AsyncMock(return_value={"status": "applied"})
_fake_comms.record_decision = AsyncMock(return_value=1)
_fake_comms.store_learning = AsyncMock()
_fake_comms.delegate_task = AsyncMock(return_value=1)
_fake_comms.request_task_result = AsyncMock(return_value={"status": "applied"})

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace(
    root_dir="/tmp/test-repo",
    ruflo=types.SimpleNamespace(enabled=False),
)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.llm_client"] = _fake_llm_mod
sys.modules["shared.comms"] = _fake_comms
sys.modules["shared.config"] = _fake_config

from perseus.self_audit import (
    AUDIT_IMMUTABLE,
    FAILURE_MODES,
    _deduplicate,
    _parse_vote,
)


def run(coro):
    return asyncio.run(coro)


def test_parse_vote_extracts_approve():
    assert _parse_vote({"answer": "I approve this fix"}) == "approve"
    assert _parse_vote({"answer": "Yes, we should fix this"}) == "approve"
    assert _parse_vote({"vote": "approve"}) == "approve"


def test_parse_vote_extracts_reject():
    assert _parse_vote({"answer": "I reject this change — it could break the pipeline"}) == "reject"
    assert _parse_vote({"answer": "No, we should not change this"}) == "reject"
    assert _parse_vote({"vote": "reject"}) == "reject"


def test_parse_vote_defaults_to_defer():
    assert _parse_vote(None) == "defer"
    assert _parse_vote({}) == "defer"
    assert _parse_vote({"answer": "I'm not sure about this"}) == "defer"


def test_deduplicate_removes_same_file_issue():
    findings = [
        {"file": "shared/comms.py", "issue": "SQL injection in interval"},
        {"file": "shared/comms.py", "issue": "SQL injection in interval"},
        {"file": "shared/comms.py", "issue": "Different issue"},
    ]
    unique = _deduplicate(findings)
    assert len(unique) == 2


def test_immutable_files_include_self():
    assert "perseus/self_audit.py" in AUDIT_IMMUTABLE
    assert "perseus/backprop.py" in AUDIT_IMMUTABLE
    assert "orchestrator.py" in AUDIT_IMMUTABLE
    assert "clawdbot/safety.py" in AUDIT_IMMUTABLE


def test_failure_modes_cover_known_patterns():
    assert "dead_code" in FAILURE_MODES
    assert "json_parsing_fragility" in FAILURE_MODES
    assert "silent_swallowing" in FAILURE_MODES
    assert "column_order_bugs" in FAILURE_MODES
    assert len(FAILURE_MODES) == 12


def test_consensus_requires_two_approvals():
    """Verify the consensus logic from _discuss_with_agents."""
    from perseus import self_audit

    finding = {
        "file": "test.py",
        "issue": "test issue",
        "severity": "medium",
        "failure_mode": "dead_code",
        "proposed_fix": "remove function",
        "fix_type": "code_edit",
        "reasoning": "never called",
    }

    # Mock call_agent_capability to return structured votes
    responses = {
        "titan": {"vote": "approve", "reason": "looks correct"},
        "clawdbot": {"vote": "approve", "reason": "safe to remove"},
        "hermes": {"vote": "defer", "reason": "not my domain"},
    }

    async def mock_call(to_agent, capability, params, timeout=30):
        return responses.get(to_agent, {"vote": "defer", "reason": "unknown agent"})

    with patch.object(self_audit, "call_agent_capability", side_effect=mock_call), \
         patch.object(self_audit, "_read_code_for_finding", return_value="# fake code"):
        result = run(self_audit._discuss_with_agents([finding]))

    assert result[0]["_consensus"] == "approved"
    assert result[0]["_votes"]["titan"] == "approve"
    assert result[0]["_votes"]["clawdbot"] == "approve"
    assert result[0]["_votes"]["hermes"] == "defer"


def test_consensus_approves_when_two_of_three_approve():
    """2/3 approve meets CONSENSUS_THRESHOLD=2, even with one reject."""
    from perseus import self_audit

    finding = {
        "file": "test.py",
        "issue": "test issue",
        "severity": "medium",
        "failure_mode": "dead_code",
        "proposed_fix": "remove function",
        "fix_type": "code_edit",
        "reasoning": "never called",
    }

    responses = {
        "titan": {"vote": "approve", "reason": "valid fix"},
        "clawdbot": {"vote": "reject", "reason": "might break site builds"},
        "hermes": {"vote": "approve", "reason": "safe"},
    }

    async def mock_call(to_agent, capability, params, timeout=30):
        return responses.get(to_agent, {"vote": "defer", "reason": ""})

    with patch.object(self_audit, "call_agent_capability", side_effect=mock_call), \
         patch.object(self_audit, "_read_code_for_finding", return_value="# fake code"):
        result = run(self_audit._discuss_with_agents([finding]))

    # 2/3 approve meets CONSENSUS_THRESHOLD=2
    assert result[0]["_consensus"] == "approved"


def test_analyze_file_returns_structured_findings():
    from perseus import self_audit

    llm_response = json.dumps({
        "findings": [
            {
                "file": "shared/comms.py",
                "line": 263,
                "issue": "SQL injection in interval string",
                "failure_mode": "schema_mismatch",
                "severity": "high",
                "proposed_fix": "Use make_interval instead of string interpolation",
                "fix_type": "code_edit",
                "reasoning": "Parameterized value inside string literal produces invalid SQL",
            }
        ]
    })

    mock_llm = AsyncMock(return_value=llm_response)
    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)):
        findings = run(self_audit._analyze_file("shared/comms.py", "def is_agent_alive(): ..."))

    assert len(findings) == 1
    assert findings[0]["failure_mode"] == "schema_mismatch"
    assert findings[0]["severity"] == "high"


def test_analyze_immutable_file_sets_no_fix():
    from perseus import self_audit

    llm_response = json.dumps({
        "findings": [
            {
                "file": "orchestrator.py",
                "line": 10,
                "issue": "Some issue",
                "failure_mode": "dead_code",
                "severity": "low",
                "proposed_fix": "delete it",
                "fix_type": "code_edit",
                "reasoning": "not used",
            }
        ]
    })

    mock_llm = AsyncMock(return_value=llm_response)
    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)):
        findings = run(self_audit._analyze_file("orchestrator.py", "class Orchestrator: ..."))

    assert len(findings) == 1
    assert findings[0]["fix_type"] == "no_fix_needed"
    assert findings[0]["_immutable"] is True


# ═══════════════════════════════════════════════════════════════
# Cycle 4 — Finding 1: dirty working-tree detection
# ═══════════════════════════════════════════════════════════════


def test_get_changed_files_includes_dirty_tree(tmp_path):
    """_get_changed_files must detect unstaged and staged changes, not just committed diffs."""
    from perseus import self_audit

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        # Simulate: no committed changes, but one unstaged and one staged file
        if cmd == ["git", "diff", "--name-only"]:
            return types.SimpleNamespace(returncode=0, stdout="shared/comms.py\n")
        elif cmd == ["git", "diff", "--cached", "--name-only"]:
            return types.SimpleNamespace(returncode=0, stdout="titan/pipeline/invoice.py\n")
        else:
            # committed diff — empty
            return types.SimpleNamespace(returncode=0, stdout="")

    with patch.object(self_audit.subprocess, "run", side_effect=fake_run), \
         patch.object(self_audit, "get_config", AsyncMock(return_value="abc123")):
        files = run(self_audit._get_changed_files(tmp_path))

    # Both dirty-tree files must appear
    assert "shared/comms.py" in files
    assert "titan/pipeline/invoice.py" in files
    # All three git diff commands must have been issued
    cmds_flat = [" ".join(c) for c in call_log]
    assert any("git diff --name-only" in s for s in cmds_flat), f"Missing committed diff: {cmds_flat}"
    assert any("git diff --cached" in s for s in cmds_flat), f"Missing staged diff: {cmds_flat}"
    # The plain 'git diff --name-only' (no range) captures unstaged changes
    assert any(s == "git diff --name-only" for s in cmds_flat), f"Missing unstaged diff: {cmds_flat}"


def test_get_changed_files_unions_all_sources(tmp_path):
    """Same file appearing in committed + unstaged must not duplicate."""
    from perseus import self_audit

    def fake_run(cmd, **kwargs):
        # Same file in committed and unstaged
        return types.SimpleNamespace(returncode=0, stdout="shared/comms.py\n")

    with patch.object(self_audit.subprocess, "run", side_effect=fake_run), \
         patch.object(self_audit, "get_config", AsyncMock(return_value="abc123")):
        files = run(self_audit._get_changed_files(tmp_path))

    assert files.count("shared/comms.py") == 1


# ═══════════════════════════════════════════════════════════════
# Cycle 4 — Finding 3: inline edit validation
# ═══════════════════════════════════════════════════════════════


def test_safe_code_edit_rolls_back_on_syntax_error(tmp_path):
    """Inline edit that produces invalid Python must be rolled back."""
    from perseus import self_audit

    target = tmp_path / "bad_edit.py"
    target.write_text("x = 1\n", encoding="utf-8")

    # LLM returns a replacement that creates a syntax error
    llm_response = json.dumps({"old_text": "x = 1", "new_text": "x = 1 +"})
    mock_llm = AsyncMock(return_value=llm_response)

    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)):
        result = run(self_audit._safe_code_edit(tmp_path, "bad_edit.py", "break syntax", "test"))

    assert result is False
    # File must be restored to original content
    assert target.read_text() == "x = 1\n"


def test_safe_code_edit_accepts_valid_edit(tmp_path):
    """Valid syntax edit should succeed."""
    from perseus import self_audit

    target = tmp_path / "good_edit.py"
    target.write_text("x = 1\n", encoding="utf-8")

    llm_response = json.dumps({"old_text": "x = 1", "new_text": "x = 2"})
    mock_llm = AsyncMock(return_value=llm_response)

    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)):
        result = run(self_audit._safe_code_edit(tmp_path, "good_edit.py", "change value", "test"))

    assert result is True
    assert "x = 2" in target.read_text()


# ═══════════════════════════════════════════════════════════════
# Cycle 4 — Finding 4: multi-file Ruflo staging
# ═══════════════════════════════════════════════════════════════


def test_apply_approved_fixes_stages_ruflo_multi_file(tmp_path):
    """When Ruflo returns multi-file proposal, all files must be staged."""
    from perseus import self_audit

    approved = [{
        "file": "shared/comms.py",
        "fix_type": "code_edit",
        "issue": "test",
        "proposed_fix": "fix it",
    }]

    # Ruflo returns applied with multi-file proposal
    ruflo_result = {
        "status": "applied",
        "proposal": {"files": ["shared/comms.py", "shared/db.py", "titan/pipeline/invoice.py"]},
    }
    mock_request = AsyncMock(return_value=ruflo_result)

    git_add_calls = []
    original_run = subprocess.run

    def capture_git_add(cmd, **kwargs):
        if cmd[:2] == ["git", "add"]:
            git_add_calls.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")  # has staged changes
        if cmd[:2] == ["git", "commit"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    import shared.comms as _comms_mod
    _orig_rtr = getattr(_comms_mod, "request_task_result", None)
    _comms_mod.request_task_result = mock_request
    try:
        with patch.object(self_audit, "config", types.SimpleNamespace(ruflo=types.SimpleNamespace(enabled=True))), \
             patch.object(self_audit.subprocess, "run", side_effect=capture_git_add):
            counts = run(self_audit._apply_approved_fixes(approved, "test-cycle", tmp_path))
    finally:
        if _orig_rtr is not None:
            _comms_mod.request_task_result = _orig_rtr

    assert counts["applied"] == 1
    # The git add call must include ALL three files from the proposal
    assert len(git_add_calls) == 1
    staged = git_add_calls[0]
    assert "shared/comms.py" in staged
    assert "shared/db.py" in staged
    assert "titan/pipeline/invoice.py" in staged


def test_apply_approved_fixes_falls_back_to_filepath_when_no_proposal_files(tmp_path):
    """When Ruflo result lacks proposal.files, fall back to original filepath."""
    from perseus import self_audit

    approved = [{
        "file": "shared/comms.py",
        "fix_type": "code_edit",
        "issue": "test",
        "proposed_fix": "fix it",
    }]

    ruflo_result = {"status": "applied"}  # no proposal.files
    mock_request = AsyncMock(return_value=ruflo_result)

    git_add_calls = []

    def capture_git_add(cmd, **kwargs):
        if cmd[:2] == ["git", "add"]:
            git_add_calls.append(cmd)
        if cmd[:2] == ["git", "diff"]:
            return types.SimpleNamespace(returncode=1)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    import shared.comms as _comms_mod
    _orig_rtr = getattr(_comms_mod, "request_task_result", None)
    _comms_mod.request_task_result = mock_request
    try:
        with patch.object(self_audit, "config", types.SimpleNamespace(ruflo=types.SimpleNamespace(enabled=True))), \
             patch.object(self_audit.subprocess, "run", side_effect=capture_git_add):
            counts = run(self_audit._apply_approved_fixes(approved, "test-cycle", tmp_path))
    finally:
        if _orig_rtr is not None:
            _comms_mod.request_task_result = _orig_rtr

    assert counts["applied"] == 1
    assert len(git_add_calls) == 1
    assert "shared/comms.py" in git_add_calls[0]


# ═══════════════════════════════════════════════════════════════
# Cycle 5 — Finding 1: untracked files invisible to self-audit
# ═══════════════════════════════════════════════════════════════


def test_get_changed_files_includes_untracked(tmp_path):
    """Brand-new .py files (untracked) must be discovered by _get_changed_files."""
    from perseus import self_audit

    def fake_run(cmd, **kwargs):
        # All diffs empty, but ls-files returns an untracked file
        if cmd[:2] == ["git", "ls-files"]:
            return types.SimpleNamespace(returncode=0, stdout="ruflo/new_agent.py\n")
        return types.SimpleNamespace(returncode=0, stdout="")

    with patch.object(self_audit.subprocess, "run", side_effect=fake_run), \
         patch.object(self_audit, "get_config", AsyncMock(return_value="abc123")):
        files = run(self_audit._get_changed_files(tmp_path))

    assert "ruflo/new_agent.py" in files


# ═══════════════════════════════════════════════════════════════
# Cycle 5 — Finding 3: inline edit validation fail-closed
# ═══════════════════════════════════════════════════════════════


def test_safe_code_edit_rolls_back_on_test_timeout(tmp_path):
    """If pytest times out, the edit must be rolled back (fail-closed)."""
    from perseus import self_audit

    target = tmp_path / "timeout_edit.py"
    original = "x = 1\n"
    target.write_text(original, encoding="utf-8")

    # Create a matching test file so the pytest path is entered
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_timeout_edit.py").write_text("def test_ok(): pass\n")

    llm_response = json.dumps({"old_text": "x = 1", "new_text": "x = 2"})
    mock_llm = AsyncMock(return_value=llm_response)

    def fake_run(cmd, **kwargs):
        if "pytest" in str(cmd):
            raise subprocess.TimeoutExpired(cmd, 30)
        return types.SimpleNamespace(returncode=0)

    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)), \
         patch.object(self_audit.subprocess, "run", side_effect=fake_run):
        result = run(self_audit._safe_code_edit(tmp_path, "timeout_edit.py", "change value", "test"))

    assert result is False
    assert target.read_text() == original


def test_safe_code_edit_rolls_back_on_test_exception(tmp_path):
    """If pytest throws an unexpected error, the edit must be rolled back."""
    from perseus import self_audit

    target = tmp_path / "crash_edit.py"
    original = "x = 1\n"
    target.write_text(original, encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_crash_edit.py").write_text("def test_ok(): pass\n")

    llm_response = json.dumps({"old_text": "x = 1", "new_text": "x = 2"})
    mock_llm = AsyncMock(return_value=llm_response)

    def fake_run(cmd, **kwargs):
        if "pytest" in str(cmd):
            raise OSError("pytest binary not found")
        return types.SimpleNamespace(returncode=0)

    with patch.object(self_audit, "llm", types.SimpleNamespace(generate=mock_llm)), \
         patch.object(self_audit.subprocess, "run", side_effect=fake_run):
        result = run(self_audit._safe_code_edit(tmp_path, "crash_edit.py", "change value", "test"))

    assert result is False
    assert target.read_text() == original
