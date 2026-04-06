"""Phase 30: T2 Model Selection tests.

Tests for:
- T2Selection dataclass
- _TASK_TYPE_HINTS verifiable flags
- select_model_t2() with T2 optimization
- execute_with_t2() concurrent execution
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# T2Selection dataclass
# ---------------------------------------------------------------------------


def test_t2_selection_defaults():
    from shared.model_selector import T2Selection

    t2 = T2Selection(model="claude-3-5-haiku-latest")
    assert t2.model == "claude-3-5-haiku-latest"
    assert t2.passes == 1
    assert t2.selection_strategy == "best"
    assert t2.verifier is None


def test_t2_selection_with_passes():
    from shared.model_selector import T2Selection

    t2 = T2Selection(model="haiku", passes=3, selection_strategy="majority", verifier="label_in_set")
    assert t2.passes == 3
    assert t2.selection_strategy == "majority"
    assert t2.verifier == "label_in_set"


# ---------------------------------------------------------------------------
# Task type hints — verifiable flags
# ---------------------------------------------------------------------------


def test_classification_is_verifiable():
    from shared.model_selector import _TASK_TYPE_HINTS

    assert _TASK_TYPE_HINTS["classification"]["verifiable"] is True
    assert _TASK_TYPE_HINTS["classification"]["pass_k_eligible"] is True
    assert _TASK_TYPE_HINTS["classification"]["verifier"] == "label_in_set"


def test_extraction_is_verifiable():
    from shared.model_selector import _TASK_TYPE_HINTS

    assert _TASK_TYPE_HINTS["extraction"]["verifiable"] is True
    assert _TASK_TYPE_HINTS["extraction"]["verifier"] == "json_schema_valid"


def test_email_subject_is_verifiable():
    from shared.model_selector import _TASK_TYPE_HINTS

    assert _TASK_TYPE_HINTS["email_subject"]["verifiable"] is True
    assert _TASK_TYPE_HINTS["email_subject"]["verifier"] == "length_and_format"


def test_code_is_verifiable():
    from shared.model_selector import _TASK_TYPE_HINTS

    assert _TASK_TYPE_HINTS["code"]["verifiable"] is True
    assert _TASK_TYPE_HINTS["code"]["verifier"] == "syntax_check"


def test_proposal_not_verifiable():
    from shared.model_selector import _TASK_TYPE_HINTS

    assert _TASK_TYPE_HINTS["proposal"]["verifiable"] is False
    assert _TASK_TYPE_HINTS["proposal"]["pass_k_eligible"] is False


def test_architecture_not_verifiable():
    from shared.model_selector import _TASK_TYPE_HINTS

    assert _TASK_TYPE_HINTS["architecture"]["verifiable"] is False


# ---------------------------------------------------------------------------
# select_model_t2()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_disabled_returns_single_pass():
    from shared.model_selector import select_model_t2

    with patch.dict(os.environ, {"T2_MODEL_SELECT": "false"}):
        base, t2 = await select_model_t2("smart", task_type="classification")
        assert t2.passes == 1


@pytest.mark.asyncio
async def test_t2_classification_uses_haiku_x3():
    """Classification tasks should use Haiku x3 instead of Sonnet x1."""
    from shared.model_selector import select_model_t2

    with patch.dict(os.environ, {"T2_MODEL_SELECT": "true"}):
        base, t2 = await select_model_t2("smart", task_type="classification")
        assert t2.passes == 3
        assert t2.selection_strategy == "majority"
        assert t2.verifier == "label_in_set"
        # The model should be from the "fast" tier
        assert "haiku" in t2.model.lower() or "fast" in base.tier


@pytest.mark.asyncio
async def test_t2_email_subject_uses_haiku_x5():
    from shared.model_selector import select_model_t2

    with patch.dict(os.environ, {"T2_MODEL_SELECT": "true"}):
        base, t2 = await select_model_t2("smart", task_type="email_subject")
        assert t2.passes == 5
        assert t2.verifier == "length_and_format"


@pytest.mark.asyncio
async def test_t2_code_uses_sonnet_x2():
    from shared.model_selector import select_model_t2

    with patch.dict(os.environ, {"T2_MODEL_SELECT": "true"}):
        base, t2 = await select_model_t2("genius", task_type="code")
        assert t2.passes == 2
        assert t2.verifier == "syntax_check"


@pytest.mark.asyncio
async def test_t2_non_verifiable_single_pass():
    from shared.model_selector import select_model_t2

    with patch.dict(os.environ, {"T2_MODEL_SELECT": "true"}):
        base, t2 = await select_model_t2("smart", task_type="proposal")
        assert t2.passes == 1


@pytest.mark.asyncio
async def test_t2_general_task_single_pass():
    from shared.model_selector import select_model_t2

    with patch.dict(os.environ, {"T2_MODEL_SELECT": "true"}):
        base, t2 = await select_model_t2("smart", task_type="general")
        assert t2.passes == 1


# ---------------------------------------------------------------------------
# execute_with_t2()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_single_pass():
    from shared.model_selector import T2Selection, execute_with_t2

    t2 = T2Selection(model="test", passes=1)

    async def gen():
        return "result"

    results = await execute_with_t2(t2, gen)
    assert results == ["result"]


@pytest.mark.asyncio
async def test_execute_multi_pass_no_verifier():
    from shared.model_selector import T2Selection, execute_with_t2

    t2 = T2Selection(model="test", passes=3)
    call_count = 0

    async def gen():
        nonlocal call_count
        call_count += 1
        return f"result_{call_count}"

    results = await execute_with_t2(t2, gen)
    assert len(results) == 3
    assert call_count == 3


@pytest.mark.asyncio
async def test_execute_multi_pass_with_verifier_best():
    from shared.model_selector import T2Selection, execute_with_t2

    t2 = T2Selection(model="test", passes=3, selection_strategy="best", verifier="test")

    async def gen():
        return "candidate"

    scores = iter([0.3, 0.9, 0.5])

    async def verify(c):
        return next(scores)

    results = await execute_with_t2(t2, gen, verify)
    assert len(results) == 3  # all returned, sorted by score


@pytest.mark.asyncio
async def test_execute_multi_pass_first_passing():
    from shared.model_selector import T2Selection, execute_with_t2

    t2 = T2Selection(model="test", passes=3, selection_strategy="first_passing", verifier="test")

    counter = 0

    async def gen():
        nonlocal counter
        counter += 1
        return f"candidate_{counter}"

    scores = iter([0.2, 0.8, 0.6])

    async def verify(c):
        return next(scores)

    results = await execute_with_t2(t2, gen, verify)
    assert len(results) == 1  # first passing (score > 0.5)


@pytest.mark.asyncio
async def test_execute_handles_generation_failures():
    from shared.model_selector import T2Selection, execute_with_t2

    t2 = T2Selection(model="test", passes=3)

    call_count = 0

    async def gen():
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("fail")
        return f"ok_{call_count}"

    results = await execute_with_t2(t2, gen)
    assert len(results) == 2  # one failed, two succeeded


@pytest.mark.asyncio
async def test_t2_max_passes_cap():
    from shared.model_selector import T2_MAX_PASSES

    assert T2_MAX_PASSES >= 1
    assert T2_MAX_PASSES <= 10  # reasonable upper bound
