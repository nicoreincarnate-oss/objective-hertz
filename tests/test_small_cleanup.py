"""Static checks for confirmed cleanup items."""

from pathlib import Path


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def test_close_deal_no_longer_calls_dead_negotiation_handler():
    code = (ROOT / "titan/pipeline/close_deal.py").read_text()
    assert "await _handle_negotiations()" not in code
    assert "async def _handle_negotiations" not in code


def test_queue_for_review_hoists_jsonb_import_outside_loop():
    code = (ROOT / "titan/pipeline/email_send.py").read_text()
    start = code.index("async def _queue_for_review")
    end = code.index("async def sync_campaign_analytics")
    section = code[start:end]
    assert "from psycopg.types.json import Jsonb" in section
    assert section.count("from psycopg.types.json import Jsonb") == 1
    loop_start = section.index("for lead in leads:")
    import_pos = section.index("from psycopg.types.json import Jsonb")
    assert import_pos < loop_start
