"""Regression tests for Instantly API throttling."""

import asyncio
import time
from unittest.mock import AsyncMock, patch


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def make_client(http_client, min_interval: float = 0.2):
    from tools.instantly_client import InstantlyClient

    client = object.__new__(InstantlyClient)
    client.api_key = "test-key"
    client._http = http_client
    client._min_interval_seconds = min_interval
    client._max_retries = 3
    return client


def test_request_retries_once_on_429():
    from tools.instantly_client import InstantlyClient

    http_client = type("FakeHTTP", (), {})()
    http_client.post = AsyncMock(
        side_effect=[
            FakeResponse(429, {"error": "rate_limited"}, {"Retry-After": "2"}),
            FakeResponse(200, {"ok": True}),
        ]
    )

    client = make_client(http_client, min_interval=0.0)

    with patch("tools.instantly_client.asyncio.sleep", AsyncMock()) as sleep:
        result = run(InstantlyClient._request(client, "post", "/leads", data={"email": "a@b.com"}))

    assert result == {"ok": True}
    sleep.assert_awaited_with(2.0)
    assert http_client.post.await_count == 2


def test_wait_for_rate_limit_slot_sleeps_when_called_too_soon():
    from tools.instantly_client import InstantlyClient

    http_client = type("FakeHTTP", (), {})()
    client = make_client(http_client, min_interval=0.2)
    InstantlyClient._rate_limit_lock = None
    InstantlyClient._global_last_request_at = time.monotonic()

    with patch("tools.instantly_client.asyncio.sleep", AsyncMock()) as sleep:
        run(InstantlyClient._wait_for_rate_limit_slot(client))

    delay = sleep.await_args.args[0]
    assert 0 < delay <= 0.2


def test_rate_limit_slot_is_shared_across_client_instances():
    from tools.instantly_client import InstantlyClient

    make_client(type("FakeHTTP", (), {})(), min_interval=0.2)
    client_two = make_client(type("FakeHTTP", (), {})(), min_interval=0.2)
    InstantlyClient._rate_limit_lock = None
    InstantlyClient._global_last_request_at = time.monotonic()

    with patch("tools.instantly_client.asyncio.sleep", AsyncMock()) as sleep:
        run(InstantlyClient._wait_for_rate_limit_slot(client_two))

    delay = sleep.await_args.args[0]
    assert 0 < delay <= 0.2
