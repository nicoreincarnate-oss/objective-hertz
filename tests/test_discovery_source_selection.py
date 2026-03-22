"""Static checks for AI-driven discovery source selection."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_lead_discovery_has_ai_source_selection():
    code = (ROOT / "titan/pipeline/lead_discovery.py").read_text()
    assert "async def _choose_discovery_sources(" in code
    assert "source_plan = await _choose_discovery_sources(" in code
    assert '"custom_firecrawl"' in code
    assert "Available sources:" in code


def test_discovery_no_longer_uses_first_installed_skill_wins_loop():
    code = (ROOT / "titan/pipeline/lead_discovery.py").read_text()
    assert "for source_name in source_plan[\"source_order\"]:" in code
