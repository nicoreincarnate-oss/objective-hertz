"""Regression tests for pipeline error visibility."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hermes_formats_pipeline_error_events():
    code = (ROOT / "hermes" / "alerts.py").read_text()

    assert '"pipeline_error": lambda p:' in code
    assert '"pipeline_stage_error": lambda p:' in code
    assert '"lead_discovery_empty": lambda p:' in code


def test_core_pipeline_stages_emit_pipeline_error_events():
    stage_files = [
        ROOT / "titan" / "pipeline" / "lead_discovery.py",
        ROOT / "titan" / "pipeline" / "lead_research.py",
        ROOT / "titan" / "pipeline" / "email_compose.py",
        ROOT / "titan" / "pipeline" / "email_send.py",
        ROOT / "titan" / "pipeline" / "follow_up.py",
        ROOT / "titan" / "pipeline" / "close_deal.py",
        ROOT / "titan" / "pipeline" / "build_site.py",
        ROOT / "titan" / "pipeline" / "invoice.py",
    ]

    for path in stage_files:
        code = path.read_text()
        assert "emit_pipeline_error(" in code, f"{path.name} should surface swallowed stage failures"


def test_lead_discovery_surfaces_zero_result_runs():
    code = (ROOT / "titan" / "pipeline" / "lead_discovery.py").read_text()

    assert 'emit_event("lead_discovery_empty"' in code
    assert '"lead_discovery.empty"' in code
