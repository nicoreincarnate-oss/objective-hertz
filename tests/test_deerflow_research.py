"""Tests for DeerFlow research daemon wiring and runtime output."""

from __future__ import annotations

import asyncio


def test_deerflow_runtime_creates_cycle_and_daily_brief(tmp_path):
    from deerflow_research.runtime import DeerFlowResearchRuntime

    runtime = DeerFlowResearchRuntime(root_dir=tmp_path)
    runtime._source_fetcher.fetch = _fake_fetch  # type: ignore[method-assign]
    runtime._engine = _FakeEngine()  # type: ignore[attr-defined]
    cycle = asyncio.run(runtime.evolution_research_cycle({"topic": "Track agent papers"}))
    brief = asyncio.run(runtime.daily_evolution_brief({"lookback_hours": 24}))

    assert cycle["ok"] is True
    assert cycle["recommended_inference_lane"] == "local-heavy"
    assert cycle["artifact"]["path"].endswith(".md")
    assert cycle["item_count"] >= 1
    assert cycle["engine"] == "deerflow_client"
    assert brief["ok"] is True
    assert brief["artifact"]["path"].endswith(".md")
    assert brief["engine"] == "deerflow_client"


def test_deerflow_a2a_parses_repo_scan_requests():
    from deerflow_research.a2a_server import handle_a2a_input

    class _StubDaemon:
        async def handle_capability(self, capability, payload):
            return {"capability": capability, "payload": payload}

    result = asyncio.run(handle_a2a_input("scan github repos for browser automation", _StubDaemon()))

    assert "repo_scan" in result


def test_parse_arxiv_feed_extracts_entries():
    from deerflow_research.sources import parse_arxiv_feed

    xml_text = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1234.5678v1</id>
        <updated>2026-04-01T01:02:03Z</updated>
        <published>2026-04-01T01:02:03Z</published>
        <title>Fresh agent paper</title>
        <summary>Long context agent research.</summary>
        <author><name>Stanford Author</name></author>
        <category term="cs.AI" />
      </entry>
    </feed>"""

    items = parse_arxiv_feed(xml_text)

    assert len(items) == 1
    assert items[0].title == "Fresh agent paper"
    assert items[0].url == "http://arxiv.org/abs/1234.5678v1"
    assert "cs.AI" in items[0].tags


def test_parse_github_repo_response_extracts_fresh_repo():
    from deerflow_research.sources import parse_github_repo_response

    items = parse_github_repo_response(
        {
            "items": [
                {
                    "full_name": "acme/agent-stack",
                    "html_url": "https://github.com/acme/agent-stack",
                    "description": "A fresh MCP agent runtime",
                    "created_at": "2026-04-01T00:00:00Z",
                    "language": "Python",
                    "stargazers_count": 42,
                    "forks_count": 7,
                }
            ]
        }
    )

    assert len(items) == 1
    assert items[0].title == "acme/agent-stack"
    assert items[0].url == "https://github.com/acme/agent-stack"
    assert "Python" in items[0].tags


async def _fake_fetch(source: str, *, topic: str, limit: int):
    return [
        _FakeItem(
            source=source,
            title=f"{source} result for {topic}",
            url=f"https://example.test/{source}",
            published_at="2026-04-01T00:00:00+00:00",
            summary="fresh result",
            tags=["fresh"],
        )
    ]


class _FakeItem:
    def __init__(self, **payload):
        self._payload = payload
        for key, value in payload.items():
            setattr(self, key, value)

    def to_dict(self):
        return dict(self._payload)


class _FakeEngine:
    async def health_check(self):
        return {"ok": True, "engine": "deerflow_client"}

    async def analyze(self, **kwargs):
        from deerflow_research.upstream import DeerFlowEngineResult

        return DeerFlowEngineResult(
            ok=True,
            engine="deerflow_client",
            analysis_markdown="## Situation\n\nFake DeerFlow analysis.",
            metadata={"thread_id": "test-thread"},
        )
