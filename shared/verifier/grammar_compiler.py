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


@dataclass
class CompiledGrammar:
    daemon: str
    tool_name: str
    schema: dict[str, Any]
    gbnf_text: str
    file_path: Path | None = None


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
    ) -> CompiledGrammar:
        gbnf = self._schema_to_gbnf(schema)
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
        if "oneOf" in schema:
            return self._compile_oneof(schema["oneOf"], rules, rule_name)
        return "string"  # Default fallback

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


__all__ = ["GrammarCompiler", "CompiledGrammar", "compile_grammar_from_schema"]
