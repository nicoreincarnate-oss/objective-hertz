from __future__ import annotations

import asyncio
import json


def test_ruflo_a2a_parses_review_request():
    from ruflo.a2a_server import handle_a2a_input

    class _StubDaemon:
        async def handle_capability(self, capability, payload):
            return {"capability": capability, "payload": payload}

    result = asyncio.run(handle_a2a_input("review this code for bugs", _StubDaemon()))
    parsed = json.loads(result)

    assert parsed["capability"] == "code_review"


def test_ruflo_a2a_defaults_to_health():
    from ruflo.a2a_server import handle_a2a_input

    class _StubDaemon:
        async def handle_capability(self, capability, payload):
            return {"capability": capability, "payload": payload}

    result = asyncio.run(handle_a2a_input("status", _StubDaemon()))
    parsed = json.loads(result)

    assert parsed["capability"] == "health_check"
