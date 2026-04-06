"""Lightweight JSON schema validation for tool parameters.

Validates tool input parameters against a JSON-schema-style dict without
requiring the ``jsonschema`` third-party package.  Supports:

- Required field presence checks
- Type validation (string, number, integer, boolean, array, object)
- One-level-deep nested object validation

Gate: ``ANATOMY_PROMPT_BUILDER`` feature flag (shared with phase 18a).
"""

from __future__ import annotations

from typing import Any

# Map JSON schema type names to Python type checks.
# "number" accepts both int and float; "integer" accepts only int.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _check_type(value: Any, expected_type: str) -> bool:
    """Return True if *value* matches the JSON schema *expected_type*."""
    # boolean is a subclass of int in Python — reject bools for number/integer
    if expected_type in ("number", "integer") and isinstance(value, bool):
        return False
    allowed = _TYPE_MAP.get(expected_type)
    if allowed is None:
        # Unknown type string — skip validation
        return True
    return isinstance(value, allowed)


def _validate_properties(
    params: dict[str, Any],
    properties: dict[str, Any],
    required: set[str],
    *,
    prefix: str = "",
) -> list[str]:
    """Validate *params* against *properties* and *required* sets.

    Returns a list of human-readable error strings (empty means valid).
    """
    errors: list[str] = []
    path = f"{prefix}." if prefix else ""

    # Check required fields
    for req in required:
        if req not in params:
            errors.append(f"Missing required field: '{path}{req}'")

    # Type-check each provided field that has a schema entry
    for key, value in params.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            # No schema entry for this key — skip (we don't reject unknown keys)
            continue

        expected = prop_schema.get("type")
        if expected is None:
            continue

        if not _check_type(value, expected):
            errors.append(
                f"Type mismatch for '{path}{key}': "
                f"expected {expected}, got {type(value).__name__}"
            )
            continue

        # One-level-deep nested object validation
        if expected == "object" and isinstance(value, dict):
            nested_props = prop_schema.get("properties")
            if nested_props:
                nested_required = set(prop_schema.get("required", []))
                errors.extend(
                    _validate_properties(
                        value,
                        nested_props,
                        nested_required,
                        prefix=f"{path}{key}",
                    )
                )

    return errors


def validate_tool_params(params: dict[str, Any], schema: dict[str, Any] | None) -> list[str]:
    """Validate *params* against a JSON-schema-style *schema* dict.

    Parameters
    ----------
    params:
        The parameters dict to validate.
    schema:
        A JSON-schema-style dict with optional ``properties`` and ``required``
        keys.  If *None* or empty, no validation is performed.

    Returns
    -------
    list[str]
        A list of error messages.  Empty means the parameters are valid.
    """
    if not schema:
        return []

    properties = schema.get("properties")
    if not properties:
        return []

    required = set(schema.get("required", []))
    return _validate_properties(params, properties, required)
