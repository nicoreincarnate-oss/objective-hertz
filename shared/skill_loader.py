"""
Skill loader for Perseus agents.
Finds and loads skills from multiple sources:
- hermes/skills/ (Perseus-specific skills)
- ~/.hermes/skills/ (Hermes Agent installed skills)
- ~/.openclaw/skills/ (OpenClaw skill repos)
- .agent/skills/ (Claude Code / Antigravity skills)

Skills are markdown files (SKILL.md) that instruct an LLM how to perform a task.
Optional SCHEMA.json alongside SKILL.md defines typed inputs/outputs for validation.

SCHEMA.json format:
{
    "name": "skill-name",
    "description": "What this skill does",
    "inputs": {"url": "string", "query": "string", "limit": "int"},
    "required_inputs": ["url"],
    "output_type": "json|markdown|text",
    "timeout_seconds": 30
}
"""

import json
import logging
from pathlib import Path
from typing import Optional

from shared.config import config
from shared.llm_client import llm

logger = logging.getLogger("perseus.skills")

# Directories to search for skills (in priority order)
SKILL_DIRS = [
    config.root_dir / "hermes" / "skills",          # Perseus custom skills
    Path.home() / ".hermes" / "skills",              # Hermes Agent skills
    Path.home() / ".openclaw" / "skills",            # OpenClaw skills (curated repos)
    config.root_dir / ".agent" / "skills",           # Claude Code / Antigravity
]

# Valid schema types for input validation
_SCHEMA_TYPES = {"string", "int", "float", "bool", "list", "dict"}


def find_skill(name: str) -> Optional[Path]:
    """
    Find a skill by name across all skill directories.
    Searches recursively for SKILL.md files in directories matching the name.
    """
    for skill_dir in SKILL_DIRS:
        if not skill_dir.exists():
            continue

        # Direct match: skill_dir/name/SKILL.md
        direct = skill_dir / name / "SKILL.md"
        if direct.exists():
            return direct

        # Recursive search through subdirectories
        for skill_file in skill_dir.rglob("SKILL.md"):
            if skill_file.parent.name == name:
                return skill_file
            # Also check if the skill name is in the file's frontmatter
            try:
                content = skill_file.read_text()
                if f"name: {name}" in content[:500]:
                    return skill_file
            except Exception:
                continue

    return None


def find_skills_by_category(category: str) -> list[tuple[str, Path]]:
    """Find all skills matching a category keyword."""
    results = []
    for skill_dir in SKILL_DIRS:
        if not skill_dir.exists():
            continue
        for skill_file in skill_dir.rglob("SKILL.md"):
            try:
                content = skill_file.read_text()[:1000]
                if category.lower() in content.lower():
                    name = skill_file.parent.name
                    results.append((name, skill_file))
            except Exception:
                continue
    return results


def load_skill(path: Path) -> str:
    """Load a skill's content from its SKILL.md file."""
    return path.read_text()


def load_skill_schema(skill_path: Path) -> Optional[dict]:
    """Load SCHEMA.json from the same directory as SKILL.md. Returns None if absent."""
    schema_path = skill_path.parent / "SCHEMA.json"
    if not schema_path.exists():
        return None
    try:
        return json.loads(schema_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Invalid SCHEMA.json at {schema_path}: {e}")
        return None


def validate_skill_inputs(schema: dict, context: dict) -> list[str]:
    """Validate context dict against a skill schema. Returns list of errors (empty = valid)."""
    errors = []
    inputs_spec = schema.get("inputs", {})
    required = set(schema.get("required_inputs", []))

    # Check required inputs are present
    for key in required:
        if key not in context or context[key] is None or str(context[key]).strip() == "":
            errors.append(f"Missing required input: '{key}'")

    # Type-check provided inputs
    type_map = {"string": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}
    for key, expected_type in inputs_spec.items():
        if key in context and context[key] is not None:
            python_type = type_map.get(expected_type)
            if python_type and not isinstance(context[key], python_type):
                # Try coercion for string inputs (common from payload dicts)
                if expected_type == "int":
                    try:
                        int(context[key])
                        continue
                    except (ValueError, TypeError):
                        pass
                elif expected_type == "float":
                    try:
                        float(context[key])
                        continue
                    except (ValueError, TypeError):
                        pass
                errors.append(f"Input '{key}' expected {expected_type}, got {type(context[key]).__name__}")

    return errors


async def execute_skill(
    skill_name: str,
    task_prompt: str,
    context: dict = None,
    model: str = "fast",
) -> str:
    """
    Execute a skill by name.
    1. Find the skill
    2. Load its instructions + optional schema
    3. Validate inputs against schema (if present)
    4. Send to LLM with the task prompt
    5. Return the result
    """
    skill_path = find_skill(skill_name)
    if not skill_path:
        logger.warning(f"Skill '{skill_name}' not found in any skill directory")
        return ""

    skill_content = load_skill(skill_path)
    schema = load_skill_schema(skill_path)
    logger.info(f"Executing skill '{skill_name}' from {skill_path}" +
                (f" (schema: {schema.get('output_type', 'any')})" if schema else " (no schema)"))

    # Validate inputs if schema exists
    if schema and context:
        errors = validate_skill_inputs(schema, context)
        if errors:
            error_msg = f"Skill '{skill_name}' input validation failed: {'; '.join(errors)}"
            logger.warning(error_msg)
            return json.dumps({"error": error_msg, "validation_errors": errors})

    # Build context string
    ctx = ""
    if context:
        ctx = "\n\nCONTEXT:\n" + "\n".join(f"- {k}: {v}" for k, v in context.items())

    # Apply schema timeout if specified
    timeout = schema.get("timeout_seconds") if schema else None

    result = await llm.generate(
        prompt=task_prompt + ctx,
        system=skill_content,
        model=model,
    )
    return result


async def execute_skill_or_fallback(
    skill_name: str,
    task_prompt: str,
    fallback_fn,
    context: dict = None,
    model: str = "fast",
):
    """
    Try to execute a skill. If not found, run the fallback function.
    This is how pipeline stages work: skill first, custom code second.
    """
    skill_path = find_skill(skill_name)
    if skill_path:
        logger.info(f"Using skill '{skill_name}' for task")
        return await execute_skill(skill_name, task_prompt, context, model)
    else:
        logger.info(f"Skill '{skill_name}' not found, using fallback code")
        return await fallback_fn()


def list_installed_skills() -> list[dict]:
    """List all installed skills across all directories."""
    skills = []
    for skill_dir in SKILL_DIRS:
        if not skill_dir.exists():
            continue
        for skill_file in skill_dir.rglob("SKILL.md"):
            try:
                content = skill_file.read_text()[:500]
                name = skill_file.parent.name
                # Extract description from frontmatter
                desc = ""
                for line in content.split("\n"):
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
                        break
                schema = load_skill_schema(skill_file)
                skills.append({
                    "name": name,
                    "description": desc,
                    "path": str(skill_file),
                    "source": str(skill_dir),
                    "has_schema": schema is not None,
                    "inputs": list(schema.get("inputs", {}).keys()) if schema else [],
                    "output_type": schema.get("output_type", "unknown") if schema else "unknown",
                })
            except Exception:
                continue
    return skills
