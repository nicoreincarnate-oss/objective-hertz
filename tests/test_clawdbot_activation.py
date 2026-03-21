"""Regression checks that ClawdBot is on the active execution path."""

from pathlib import Path


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_lead_discovery_routes_skill_execution_through_clawdbot():
    content = _read("titan/pipeline/lead_discovery.py")
    assert "request_task_result" in content
    assert '"skill_execute"' in content


def test_lead_research_routes_scrape_and_enrichment_through_clawdbot():
    content = _read("titan/pipeline/lead_research.py")
    assert "request_task_result" in content
    assert '"web_scrape"' in content
    assert '"enrich_lead"' in content


def test_deploy_site_routes_verification_through_clawdbot():
    content = _read("titan/pipeline/deploy_site.py")
    assert "request_task_result" in content
    assert '"verify_single_site"' in content


def test_clawdbot_emits_generic_task_results():
    content = _read("clawdbot/daemon.py")
    assert 'await db.emit_event("task_result"' in content
    assert '"request_id": request_id' in content
