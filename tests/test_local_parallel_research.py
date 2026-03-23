"""Static checks for local-first and parallel research flow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_lead_research_parallelizes_work_and_has_smart_escalation():
    code = (ROOT / "titan/pipeline/lead_research.py").read_text()
    assert "asyncio.gather(" in code
    assert 'model="fast"' in code
    assert 'model="smart"' in code
    assert "request_task_result(" in code
