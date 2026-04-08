"""Layer 1: Grammar-constrained generation.

Compiles JSON schemas (the same ones daemons declare for tool calls) into
GBNF grammars that the local model is forced to follow at the token level.
This eliminates the entire class of "wrong shape JSON" failures.

GBNF format is supported by llama.cpp's grammar feature and outlines (which
wraps mlx_lm.server). At startup we walk every daemon's tool registry, compile
each schema once, and cache the .gbnf file on disk for fast reload.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("perseus.verifier.grammar")


class UnsupportedSchemaFeatureError(ValueError):
    """Raised when a JSON schema uses a feature GBNF compilation can't express.

    The grammar compiler supports a practical subset of JSON Schema. Features
    like ``$ref``, ``$defs``, ``allOf``, ``pattern``, and complex ``anyOf``
    cannot be safely expressed as GBNF productions.

    Callers that catch this error should fall back to JSON-mode generation
    (no grammar constraint) plus post-hoc Pydantic validation. This preserves
    the correctness guarantee without silently degrading the constrained
    decoding contract into a vacuous "any string" production.
    """


@dataclass
class CompiledGrammar:
    daemon: str
    tool_name: str
    schema: dict[str, Any]
    gbnf_text: str
    file_path: Path | None = None
    # When True, callers MUST NOT use gbnf_text for constrained decoding;
    # they should fall back to JSON-mode + post-hoc validation.
    unsupported: bool = False
    unsupported_reason: str | None = None


class GrammarCompiler:
    """Compile JSON schemas into GBNF grammar text.

    Supports a useful subset of JSON Schema:
    - object / array / string / number / boolean / null
    - properties + required
    - enum
    - oneOf (limited — only string-tagged unions)
    - additionalProperties: false
    """

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or Path("/opt/perseus/runtime/grammars")
        self.compiled: dict[str, CompiledGrammar] = {}

    def compile(
        self,
        schema: dict[str, Any],
        *,
        daemon: str,
        tool_name: str,
        strict: bool = True,
    ) -> CompiledGrammar:
        """Compile a schema into a CompiledGrammar.

        If the schema uses an unsupported JSON Schema feature:
        - ``strict=True`` (default): re-raise ``UnsupportedSchemaFeatureError``
          annotated with daemon/tool context so the caller can fall back to
          JSON-mode + post-hoc Pydantic validation.
        - ``strict=False``: return a CompiledGrammar with ``unsupported=True``
          and an empty ``gbnf_text`` so the caller can decide at runtime.
        """
        try:
            gbnf = self._schema_to_gbnf(schema)
        except UnsupportedSchemaFeatureError as exc:
            msg = f"Cannot compile {daemon}.{tool_name}: {exc}"
            logger.warning(
                "grammar_compile_unsupported daemon=%s tool=%s reason=%s",
                daemon,
                tool_name,
                exc,
            )
            if strict:
                raise UnsupportedSchemaFeatureError(msg) from exc
            grammar = CompiledGrammar(
                daemon=daemon,
                tool_name=tool_name,
                schema=schema,
                gbnf_text="",
                unsupported=True,
                unsupported_reason=str(exc),
            )
            self.compiled[f"{daemon}.{tool_name}"] = grammar
            return grammar

        grammar = CompiledGrammar(
            daemon=daemon,
            tool_name=tool_name,
            schema=schema,
            gbnf_text=gbnf,
        )
        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            file_path = self.output_dir / f"{daemon}_{tool_name}.gbnf"
            file_path.write_text(gbnf)
            grammar.file_path = file_path
        cache_key = f"{daemon}.{tool_name}"
        self.compiled[cache_key] = grammar
        return grammar

    def get(self, daemon: str, tool_name: str) -> CompiledGrammar | None:
        return self.compiled.get(f"{daemon}.{tool_name}")

    # ─── GBNF generator ─────────────────────────────────────────────────

    def _schema_to_gbnf(self, schema: dict[str, Any]) -> str:
        """Convert a JSON schema dict to GBNF grammar text."""
        rules: dict[str, str] = {}
        rules["root"] = self._compile_rule(schema, rules, "root")
        # Add JSON primitives
        rules.setdefault("ws", "([ \\t\\n] ws | \"\")")
        rules.setdefault("string", "\"\\\"\" ([^\"\\\\] | \"\\\\\" .)* \"\\\"\"")
        rules.setdefault("number", "(\"-\"? ([0-9] | [1-9] [0-9]+) (\".\" [0-9]+)? ([eE] [-+]? [0-9]+)?)")
        rules.setdefault("boolean", "(\"true\" | \"false\")")
        rules.setdefault("null", "\"null\"")
        return "\n".join(f"{name} ::= {body}" for name, body in rules.items())

    def _compile_rule(self, schema: dict[str, Any], rules: dict[str, str], rule_name: str) -> str:
        # ─── Unsupported feature guards (raise, never silently degrade) ──
        if "$ref" in schema:
            raise UnsupportedSchemaFeatureError(
                "$ref not supported in GBNF compilation; use JSON-mode + post-hoc validation"
            )
        if "$defs" in schema or "definitions" in schema:
            raise UnsupportedSchemaFeatureError(
                "$defs/definitions not supported in GBNF compilation; use JSON-mode + post-hoc validation"
            )
        if "allOf" in schema:
            raise UnsupportedSchemaFeatureError(
                "allOf not supported (requires schema merging); use JSON-mode + post-hoc validation"
            )
        if "pattern" in schema:
            raise UnsupportedSchemaFeatureError(
                "pattern (regex) not supported in GBNF compilation; use JSON-mode + post-hoc validation"
            )

        # ─── const → literal production ──────────────────────────────────
        if "const" in schema:
            return self._compile_const(schema["const"])

        # ─── anyOf → treat as oneOf for simple non-overlapping unions ────
        if "anyOf" in schema:
            options = schema["anyOf"]
            if not self._is_simple_tagged_union(options):
                raise UnsupportedSchemaFeatureError(
                    "anyOf with overlapping/complex types not supported; "
                    "use JSON-mode + post-hoc validation"
                )
            return self._compile_oneof(options, rules, rule_name)

        if "oneOf" in schema:
            return self._compile_oneof(schema["oneOf"], rules, rule_name)

        schema_type = schema.get("type")

        if schema_type == "object":
            return self._compile_object(schema, rules, rule_name)
        if schema_type == "array":
            return self._compile_array(schema, rules, rule_name)
        if schema_type == "string":
            if "enum" in schema:
                return self._compile_enum(schema["enum"])
            return "string"
        if schema_type == "number" or schema_type == "integer":
            return "number"
        if schema_type == "boolean":
            return "boolean"
        if schema_type == "null":
            return "null"

        # No recognized handler — raise instead of silently returning "string".
        raise UnsupportedSchemaFeatureError(
            f"Unrecognized or missing schema type: {schema_type!r} "
            f"(schema keys: {sorted(schema.keys())})"
        )

    @staticmethod
    def _compile_const(value: Any) -> str:
        """Compile a JSON Schema ``const`` into a literal GBNF production."""
        # Serialize with json.dumps to get properly escaped JSON.
        literal = json.dumps(value)
        # Escape for GBNF string literal: backslash and double-quote.
        escaped = literal.replace("\\", "\\\\").replace("\"", "\\\"")
        return f'"{escaped}"'

    @staticmethod
    def _is_simple_tagged_union(options: list[dict]) -> bool:
        """Return True if ``anyOf`` options are safe to compile as ``oneOf``.

        "Simple" means each option has a distinct primitive ``type`` or is an
        object (we trust ordered-choice matching to work). We reject when
        multiple options share the same primitive type without a
        discriminator, because GBNF alternation would be ambiguous.
        """
        if not options or not all(isinstance(o, dict) for o in options):
            return False
        seen_primitive_types: set[str] = set()
        for opt in options:
            t = opt.get("type")
            if t in {"string", "number", "integer", "boolean", "null"}:
                if t in seen_primitive_types and "enum" not in opt and "const" not in opt:
                    return False
                seen_primitive_types.add(t)
        return True

    def _compile_object(self, schema: dict, rules: dict, rule_name: str) -> str:
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        if not properties:
            return "\"{}\""

        prop_rules: list[str] = []
        for i, (key, prop_schema) in enumerate(properties.items()):
            sub_rule_name = f"{rule_name}_{key}"
            sub_rule = self._compile_rule(prop_schema, rules, sub_rule_name)
            if sub_rule not in ("string", "number", "boolean", "null"):
                rules[sub_rule_name] = sub_rule
                ref = sub_rule_name
            else:
                ref = sub_rule
            comma = " \",\" ws " if i < len(properties) - 1 else ""
            prop_rules.append(f'"\\"{key}\\"" ws ":" ws {ref}{comma}')

        body = " ".join(prop_rules)
        return f'"{{" ws {body} ws "}}"'

    def _compile_array(self, schema: dict, rules: dict, rule_name: str) -> str:
        items_schema = schema.get("items", {"type": "string"})
        item_rule_name = f"{rule_name}_item"
        item_rule = self._compile_rule(items_schema, rules, item_rule_name)
        if item_rule not in ("string", "number", "boolean", "null"):
            rules[item_rule_name] = item_rule
            ref = item_rule_name
        else:
            ref = item_rule
        return f'"[" ws ({ref} (ws "," ws {ref})*)? ws "]"'

    def _compile_enum(self, values: list[Any]) -> str:
        quoted = [f'"\\"{v}\\""' for v in values]
        return "(" + " | ".join(quoted) + ")"

    def _compile_oneof(self, options: list[dict], rules: dict, rule_name: str) -> str:
        choices: list[str] = []
        for i, option in enumerate(options):
            sub_rule = self._compile_rule(option, rules, f"{rule_name}_opt{i}")
            choices.append(sub_rule)
        return "(" + " | ".join(choices) + ")"


_default_compiler: GrammarCompiler | None = None


def compile_grammar_from_schema(
    schema: dict[str, Any],
    *,
    daemon: str,
    tool_name: str,
    output_dir: Path | None = None,
) -> CompiledGrammar:
    global _default_compiler
    if _default_compiler is None:
        _default_compiler = GrammarCompiler(output_dir=output_dir)
    return _default_compiler.compile(schema, daemon=daemon, tool_name=tool_name)


__all__ = [
    "GrammarCompiler",
    "CompiledGrammar",
    "UnsupportedSchemaFeatureError",
    "compile_grammar_from_schema",
]


if __name__ == "__main__":
    # ─── Smoke tests ────────────────────────────────────────────────────
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        c = GrammarCompiler(output_dir=Path(tmp))

        # 1. Simple object still compiles.
        simple = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "status": {"type": "string", "enum": ["active", "idle"]},
            },
            "required": ["name"],
        }
        g = c.compile(simple, daemon="test", tool_name="simple")
        assert not g.unsupported
        assert "string" in g.gbnf_text
        assert '"active"' in g.gbnf_text
        print("PASS: simple object compiles")

        # 2. const literal compiles.
        const_schema = {
            "type": "object",
            "properties": {"kind": {"const": "ping"}},
            "required": ["kind"],
        }
        g = c.compile(const_schema, daemon="test", tool_name="const")
        assert '\\"ping\\"' in g.gbnf_text
        print("PASS: const literal compiles")

        # 3. Simple anyOf (distinct primitive types) compiles.
        anyof_simple = {"anyOf": [{"type": "string"}, {"type": "number"}]}
        g = c.compile(anyof_simple, daemon="test", tool_name="anyof_simple")
        assert not g.unsupported
        print("PASS: simple anyOf compiles")

        # 4. Unsupported features raise with daemon/tool context.
        for feature_name, schema in [
            ("$ref", {"$ref": "#/definitions/Foo"}),
            ("$defs", {"$defs": {"Foo": {"type": "string"}}, "type": "object"}),
            ("allOf", {"allOf": [{"type": "object"}, {"type": "object"}]}),
            ("pattern", {"type": "string", "pattern": "^[a-z]+$"}),
            (
                "complex_anyOf",
                {"anyOf": [{"type": "string"}, {"type": "string"}]},
            ),
            ("untyped", {"description": "no type"}),
        ]:
            try:
                c.compile(schema, daemon="test", tool_name=f"bad_{feature_name}")
            except UnsupportedSchemaFeatureError as exc:
                assert "test.bad_" in str(exc), f"missing context for {feature_name}: {exc}"
                print(f"PASS: {feature_name} raises UnsupportedSchemaFeatureError")
            else:
                raise AssertionError(f"{feature_name} should have raised")

        # 5. strict=False returns flagged grammar instead of raising.
        g = c.compile(
            {"$ref": "#/foo"},
            daemon="test",
            tool_name="nonstrict",
            strict=False,
        )
        assert g.unsupported and g.gbnf_text == ""
        assert g.unsupported_reason
        print("PASS: strict=False returns unsupported grammar")

        print("\nAll grammar_compiler tests passed.")
