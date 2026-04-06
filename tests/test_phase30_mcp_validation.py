"""Phase 30: MCP Protocol Validation tests.

Tests for:
- validate_request() — JSON-RPC 2.0 compliance, size limits, field validation
- validate_response() — result/error exclusivity, error structure
- MCPValidationError
"""

from __future__ import annotations

import json

import pytest

from openjarvis.mcp.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    MAX_PAYLOAD_BYTES,
    PARSE_ERROR,
    MCPRequest,
    MCPResponse,
    MCPValidationError,
    validate_request,
    validate_response,
)

# ---------------------------------------------------------------------------
# validate_request — happy path
# ---------------------------------------------------------------------------


class TestValidateRequest:
    def test_valid_request_from_string(self):
        raw = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1})
        req = validate_request(raw)
        assert isinstance(req, MCPRequest)
        assert req.method == "tools/list"
        assert req.params == {}
        assert req.id == 1

    def test_valid_request_from_dict(self):
        raw = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "git"}, "id": 42}
        req = validate_request(raw)
        assert req.method == "tools/call"
        assert req.params == {"name": "git"}

    def test_valid_request_from_bytes(self):
        raw = json.dumps({"jsonrpc": "2.0", "method": "ping", "id": 0}).encode()
        req = validate_request(raw)
        assert req.method == "ping"

    def test_valid_request_default_params(self):
        raw = {"jsonrpc": "2.0", "method": "status", "id": 1}
        req = validate_request(raw)
        assert req.params == {}

    # ---------------------------------------------------------------------------
    # validate_request — error cases
    # ---------------------------------------------------------------------------

    def test_rejects_oversized_payload(self):
        big = json.dumps({"jsonrpc": "2.0", "method": "x", "params": {"data": "A" * MAX_PAYLOAD_BYTES}})
        with pytest.raises(MCPValidationError) as exc_info:
            validate_request(big)
        assert exc_info.value.code == INVALID_REQUEST
        assert "limit" in exc_info.value.message

    def test_rejects_invalid_json(self):
        with pytest.raises(MCPValidationError) as exc_info:
            validate_request("{not json")
        assert exc_info.value.code == PARSE_ERROR

    def test_rejects_wrong_jsonrpc_version(self):
        raw = {"jsonrpc": "1.0", "method": "test"}
        with pytest.raises(MCPValidationError) as exc_info:
            validate_request(raw)
        assert exc_info.value.code == INVALID_REQUEST
        assert "2.0" in exc_info.value.message

    def test_rejects_missing_method(self):
        raw = {"jsonrpc": "2.0", "params": {}}
        with pytest.raises(MCPValidationError) as exc_info:
            validate_request(raw)
        assert exc_info.value.code == INVALID_REQUEST
        assert "method" in exc_info.value.message

    def test_rejects_non_dict_params(self):
        raw = {"jsonrpc": "2.0", "method": "test", "params": [1, 2, 3]}
        with pytest.raises(MCPValidationError) as exc_info:
            validate_request(raw)
        assert exc_info.value.code == INVALID_PARAMS

    def test_rejects_non_string_method(self):
        raw = {"jsonrpc": "2.0", "method": 123}
        with pytest.raises(MCPValidationError) as exc_info:
            validate_request(raw)
        assert exc_info.value.code == INVALID_REQUEST

    def test_rejects_unsupported_type(self):
        with pytest.raises(MCPValidationError) as exc_info:
            validate_request(12345)
        assert exc_info.value.code == PARSE_ERROR


# ---------------------------------------------------------------------------
# validate_response
# ---------------------------------------------------------------------------


class TestValidateResponse:
    def test_valid_result_response(self):
        raw = {"jsonrpc": "2.0", "result": {"status": "ok"}, "id": 1}
        resp = validate_response(raw)
        assert isinstance(resp, MCPResponse)
        assert resp.result == {"status": "ok"}
        assert resp.error is None

    def test_valid_error_response(self):
        raw = {"jsonrpc": "2.0", "error": {"code": -32600, "message": "bad"}, "id": 1}
        resp = validate_response(raw)
        assert resp.error is not None
        assert resp.result is None

    def test_rejects_both_result_and_error(self):
        raw = {"jsonrpc": "2.0", "result": "ok", "error": {"code": -1, "message": "x"}, "id": 1}
        with pytest.raises(MCPValidationError) as exc_info:
            validate_response(raw)
        assert "exactly one" in exc_info.value.message

    def test_rejects_neither_result_nor_error(self):
        raw = {"jsonrpc": "2.0", "id": 1}
        with pytest.raises(MCPValidationError) as exc_info:
            validate_response(raw)
        assert "exactly one" in exc_info.value.message

    def test_rejects_malformed_error(self):
        raw = {"jsonrpc": "2.0", "error": {"code": -1}, "id": 1}  # missing message
        with pytest.raises(MCPValidationError) as exc_info:
            validate_response(raw)
        assert "'error'" in exc_info.value.message

    def test_rejects_wrong_version(self):
        raw = {"jsonrpc": "1.0", "result": "ok", "id": 1}
        with pytest.raises(MCPValidationError) as exc_info:
            validate_response(raw)
        assert "2.0" in exc_info.value.message

    def test_rejects_oversized_response(self):
        big = json.dumps({"jsonrpc": "2.0", "result": "x" * MAX_PAYLOAD_BYTES, "id": 1})
        with pytest.raises(MCPValidationError) as exc_info:
            validate_response(big)
        assert "limit" in exc_info.value.message

    def test_response_from_string(self):
        raw = json.dumps({"jsonrpc": "2.0", "result": 42, "id": 5})
        resp = validate_response(raw)
        assert resp.result == 42
        assert resp.id == 5


# ---------------------------------------------------------------------------
# MCPValidationError
# ---------------------------------------------------------------------------


def test_validation_error_attrs():
    err = MCPValidationError(-32700, "bad parse")
    assert err.code == -32700
    assert err.message == "bad parse"
    assert "-32700" in str(err)
    assert "bad parse" in str(err)
