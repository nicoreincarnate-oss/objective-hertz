"""MCP JSON-RPC 2.0 protocol message types."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Error codes per JSON-RPC 2.0 / MCP spec
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP-specific error codes
SESSION_EXPIRED = -32001


@dataclass
class MCPRequest:
    """JSON-RPC 2.0 request message."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str = 0
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "jsonrpc": self.jsonrpc,
                "id": self.id,
                "method": self.method,
                "params": self.params,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> MCPRequest:
        """Deserialize from JSON string."""
        parsed = json.loads(data)
        return cls(
            method=parsed["method"],
            params=parsed.get("params", {}),
            id=parsed.get("id", 0),
            jsonrpc=parsed.get("jsonrpc", "2.0"),
        )


@dataclass
class MCPResponse:
    """JSON-RPC 2.0 response message."""

    result: Any = None
    error: dict[str, Any] | None = None
    id: int | str = 0
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        """Serialize to JSON string."""
        obj: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            obj["error"] = self.error
        else:
            obj["result"] = self.result
        return json.dumps(obj)

    @classmethod
    def from_json(cls, data: str) -> MCPResponse:
        """Deserialize from JSON string."""
        parsed = json.loads(data)
        return cls(
            result=parsed.get("result"),
            error=parsed.get("error"),
            id=parsed.get("id", 0),
            jsonrpc=parsed.get("jsonrpc", "2.0"),
        )

    @classmethod
    def error_response(
        cls,
        id: int | str,
        code: int,
        message: str,
        data: Any = None,
    ) -> MCPResponse:
        """Create an error response."""
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return cls(error=error, id=id)


@dataclass
class MCPNotification:
    """JSON-RPC 2.0 notification (no id, no response expected)."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "jsonrpc": self.jsonrpc,
                "method": self.method,
                "params": self.params,
            }
        )


@dataclass
class MCPError(Exception):
    """MCP protocol error with JSON-RPC error code."""

    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"MCPError({self.code}): {self.message}"


# ---------------------------------------------------------------------------
# Phase 30: Protocol Validation (MAS governance)
# ---------------------------------------------------------------------------

# AEGIS audit: 50KB max payload per scraped content rule
MAX_PAYLOAD_BYTES = 50 * 1024


class MCPValidationError(Exception):
    """Raised when an MCP message fails structural validation."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"MCPValidationError({code}): {message}")


def validate_request(raw: str | bytes | dict[str, Any]) -> MCPRequest:
    """Parse and validate an incoming MCP request.

    Checks:
    - Payload size limit (50KB max per AEGIS audit)
    - Valid JSON
    - JSON-RPC 2.0 compliance (jsonrpc field)
    - Required fields: method, params (if present must be dict)

    Raises MCPValidationError on failure.
    Returns a validated MCPRequest on success.
    """
    # Size check
    if isinstance(raw, (str, bytes)):
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(raw_bytes) > MAX_PAYLOAD_BYTES:
            raise MCPValidationError(
                INVALID_REQUEST,
                f"payload exceeds {MAX_PAYLOAD_BYTES} byte limit "
                f"({len(raw_bytes)} bytes)",
            )
        try:
            parsed = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MCPValidationError(PARSE_ERROR, f"invalid JSON: {exc}") from exc
    elif isinstance(raw, dict):
        parsed = raw
    else:
        raise MCPValidationError(PARSE_ERROR, f"unsupported input type: {type(raw).__name__}")

    if not isinstance(parsed, dict):
        raise MCPValidationError(INVALID_REQUEST, "request must be a JSON object")

    # JSON-RPC 2.0 compliance
    jsonrpc = parsed.get("jsonrpc", "")
    if jsonrpc != "2.0":
        raise MCPValidationError(
            INVALID_REQUEST,
            f"jsonrpc must be '2.0', got '{jsonrpc}'",
        )

    # Required field: method
    method = parsed.get("method")
    if not method or not isinstance(method, str):
        raise MCPValidationError(INVALID_REQUEST, "missing or invalid 'method' field")

    # Params must be dict if present
    params = parsed.get("params", {})
    if not isinstance(params, dict):
        raise MCPValidationError(INVALID_PARAMS, "'params' must be an object")

    return MCPRequest(
        method=method,
        params=params,
        id=parsed.get("id", 0),
        jsonrpc="2.0",
    )


def validate_response(raw: str | bytes | dict[str, Any]) -> MCPResponse:
    """Parse and validate an incoming MCP response.

    Checks:
    - Payload size limit (50KB max)
    - Valid JSON
    - JSON-RPC 2.0 compliance
    - Must have either 'result' or 'error' (not both, not neither)

    Raises MCPValidationError on failure.
    Returns a validated MCPResponse on success.
    """
    if isinstance(raw, (str, bytes)):
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(raw_bytes) > MAX_PAYLOAD_BYTES:
            raise MCPValidationError(
                INVALID_REQUEST,
                f"response exceeds {MAX_PAYLOAD_BYTES} byte limit",
            )
        try:
            parsed = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MCPValidationError(PARSE_ERROR, f"invalid JSON: {exc}") from exc
    elif isinstance(raw, dict):
        parsed = raw
    else:
        raise MCPValidationError(PARSE_ERROR, f"unsupported input type: {type(raw).__name__}")

    if not isinstance(parsed, dict):
        raise MCPValidationError(INVALID_REQUEST, "response must be a JSON object")

    jsonrpc = parsed.get("jsonrpc", "")
    if jsonrpc != "2.0":
        raise MCPValidationError(
            INVALID_REQUEST,
            f"jsonrpc must be '2.0', got '{jsonrpc}'",
        )

    has_result = "result" in parsed
    has_error = "error" in parsed
    if has_result == has_error:
        raise MCPValidationError(
            INVALID_REQUEST,
            "response must have exactly one of 'result' or 'error'",
        )

    if has_error:
        error = parsed["error"]
        if not isinstance(error, dict) or "code" not in error or "message" not in error:
            raise MCPValidationError(
                INVALID_REQUEST,
                "'error' must be an object with 'code' and 'message'",
            )

    return MCPResponse(
        result=parsed.get("result"),
        error=parsed.get("error"),
        id=parsed.get("id", 0),
        jsonrpc="2.0",
    )


__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "MAX_PAYLOAD_BYTES",
    "MCPError",
    "MCPNotification",
    "MCPRequest",
    "MCPResponse",
    "MCPValidationError",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "SESSION_EXPIRED",
    "validate_request",
    "validate_response",
]
