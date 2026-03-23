"""Tests for the external intelligence scout."""

import asyncio
import json
import types
import sys
from unittest.mock import AsyncMock, patch

# Fake modules so scout.py can import without real DB/LLM
_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.get_config = AsyncMock(return_value={})
_fake_db.set_config = AsyncMock()
_fake_db.execute = AsyncMock()

_fake_llm_mod = types.ModuleType("shared.llm_client")
_fake_llm_mod.llm = types.SimpleNamespace(generate=AsyncMock(return_value="[]"))

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.store_learning = AsyncMock()
_fake_comms.record_decision = AsyncMock(return_value=1)
_fake_comms.delegate_task = AsyncMock(return_value=1)

_fake_memory = types.ModuleType("titan.memory")
_fake_memory.store_memory = AsyncMock()

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace()

_fake_firecrawl = types.ModuleType("tools.firecrawl_client")
_fake_firecrawl.search_web = lambda *a, **kw: {"status": True, "results": []}
_fake_firecrawl.scrape_url = lambda *a, **kw: {"status": True, "content": {"title": "", "markdown_excerpt": ""}}

sys.modules.setdefault("shared.db", _fake_db)
sys.modules.setdefault("shared.llm_client", _fake_llm_mod)
sys.modules.setdefault("shared.comms", _fake_comms)
sys.modules.setdefault("shared.config", _fake_config)
sys.modules.setdefault("titan.memory", _fake_memory)
sys.modules.setdefault("tools.firecrawl_client", _fake_firecrawl)

from perseus.scout import (
    SCOUT_TOPICS,
    _dedup_urls,
    _evaluate_findings,
    _select_topics_for_cycle,
    _store_and_act,
    _url_hash,
)


def run(coro):
    return asyncio.run(coro)


def test_select_topics_rotates_through_all_stages():
    """After enough cycles, every stage should have been selected."""
    from perseus import scout
    mock_get = AsyncMock(return_value={"last_index": 0})
    mock_set = AsyncMock()

    with patch.object(scout, "get_config", mock_get), patch.object(scout, "set_config", mock_set):
        selected = run(_select_topics_for_cycle(SCOUT_TOPICS, max_topics=5))

    assert len(selected) == 5
    for stage, query in selected:
        assert stage in SCOUT_TOPICS
        assert query in SCOUT_TOPICS[stage]

    mock_set.assert_awaited_once()
    saved = mock_set.call_args[0]
    assert saved[0] == "scout_topic_rotation"
    assert saved[1]["last_index"] == 5


def test_select_topics_wraps_around():
    """When index exceeds total topics, it wraps to beginning."""
    from perseus import scout
    total_topics = sum(len(q) for q in SCOUT_TOPICS.values())
    mock_get = AsyncMock(return_value={"last_index": total_topics - 2})
    mock_set = AsyncMock()

    with patch.object(scout, "get_config", mock_get), patch.object(scout, "set_config", mock_set):
        selected = run(_select_topics_for_cycle(SCOUT_TOPICS, max_topics=5))

    assert len(selected) == 5
    saved_index = mock_set.call_args[0][1]["last_index"]
    assert saved_index < total_topics


def test_dedup_skips_already_seen_urls():
    import time
    from perseus import scout

    seen = {_url_hash("https://example.com/old"): time.time()}
    mock_get = AsyncMock(return_value=seen)
    mock_set = AsyncMock()

    results = [
        {"url": "https://example.com/old", "title": "Old"},
        {"url": "https://example.com/new", "title": "New"},
    ]

    with patch.object(scout, "get_config", mock_get), patch.object(scout, "set_config", mock_set):
        new = run(_dedup_urls(results))

    assert len(new) == 1
    assert new[0]["url"] == "https://example.com/new"


def test_dedup_caps_at_max_entries():
    import time
    from perseus import scout

    seen = {_url_hash(f"https://example.com/{i}"): time.time() - i for i in range(2100)}
    mock_get = AsyncMock(return_value=seen)
    mock_set = AsyncMock()

    with patch.object(scout, "get_config", mock_get), patch.object(scout, "set_config", mock_set):
        new = run(_dedup_urls([{"url": "https://brand-new.com", "title": "Fresh"}]))

    assert len(new) == 1
    saved = mock_set.call_args[0][1]
    assert len(saved) <= 2001


def test_evaluate_findings_filters_irrelevant():
    from perseus import scout
    llm_response = json.dumps([
        {"relevant": True, "pipeline_stage": "site_building", "action_type": "tool_to_try",
         "summary": "New API for site gen", "estimated_value": "high", "estimated_cost": "moderate"},
        {"relevant": False, "action_type": "irrelevant", "summary": "",
         "estimated_value": "low", "estimated_cost": "free"},
    ])
    mock_llm = types.SimpleNamespace(generate=AsyncMock(return_value=llm_response))

    findings = [
        {"title": "Cool AI builder", "url": "https://a.com", "description": "builds sites"},
        {"title": "Cat memes 2026", "url": "https://b.com", "description": "funny cats"},
    ]

    with patch.object(scout, "llm", mock_llm):
        evaluated = run(_evaluate_findings(findings))

    assert len(evaluated) == 2
    relevant = [e for e in evaluated if e.get("relevant")]
    assert len(relevant) == 1
    assert relevant[0]["action_type"] == "tool_to_try"
    assert relevant[0]["source_origin"] == "scout"


def test_store_and_act_dispatches_tool_findings():
    from perseus import scout

    mock_store = AsyncMock()
    mock_record = AsyncMock(return_value=1)
    mock_delegate = AsyncMock(return_value=1)
    mock_emit = AsyncMock(return_value=1)
    mock_mem = AsyncMock()

    findings = [
        {
            "relevant": True,
            "url": "https://newbuilder.ai",
            "title": "NewBuilder AI",
            "summary": "Has a REST API for site generation",
            "action_type": "tool_to_try",
            "pipeline_stage": "site_building",
            "estimated_value": "high",
            "estimated_cost": "moderate",
            "source": "producthunt.com",
        },
    ]

    with patch("shared.comms.store_learning", mock_store), \
         patch("shared.comms.record_decision", mock_record), \
         patch("shared.comms.delegate_task", mock_delegate), \
         patch.object(scout, "emit_event", mock_emit), \
         patch("titan.memory.store_memory", mock_mem):
        counts = run(_store_and_act(findings))

    assert counts["tools_dispatched"] == 1
    assert counts["stored"] == 1
    mock_delegate.assert_awaited_once()
    call_args = mock_delegate.call_args
    assert call_args[0][1] == "clawdbot"
    assert call_args[0][2] == "capability_resolve"


def test_store_and_act_stores_techniques_not_dispatches():
    from perseus import scout

    mock_store = AsyncMock()
    mock_record = AsyncMock(return_value=1)
    mock_delegate = AsyncMock(return_value=1)
    mock_emit = AsyncMock(return_value=1)
    mock_execute = AsyncMock()

    findings = [
        {
            "relevant": True,
            "url": "https://blog.example/cold-email-tips",
            "title": "Cold email trick",
            "summary": "Use single-question subject lines for 2x reply rate",
            "action_type": "technique_to_adopt",
            "pipeline_stage": "email_compose",
            "estimated_value": "medium",
            "estimated_cost": "free",
            "source": "reddit.com",
        },
    ]

    rule_json = json.dumps({"rule_text": "Subject lines must be a single question", "metric_name": "reply_rate", "category": "email_compose"})
    mock_llm = types.SimpleNamespace(generate=AsyncMock(return_value=rule_json))

    with patch("shared.comms.store_learning", mock_store), \
         patch("shared.comms.record_decision", mock_record), \
         patch("shared.comms.delegate_task", mock_delegate), \
         patch.object(scout, "emit_event", mock_emit), \
         patch.object(scout, "llm", mock_llm), \
         patch("shared.db.execute", mock_execute):
        counts = run(_store_and_act(findings))

    assert counts["techniques_stored"] == 1
    assert counts["tools_dispatched"] == 0
