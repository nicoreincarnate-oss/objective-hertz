"""
Skill loader for Perseus agents.
Finds and loads skills from multiple sources:
- hermes/skills/ (Perseus-specific skills)
- ~/.hermes/skills/ (Hermes Agent installed skills)
- ~/.openclaw/skills/ (OpenClaw skill repos)
- .agent/skills/ (Claude Code / Antigravity skills)

Skills are markdown files (SKILL.md) that instruct an LLM how to perform a task.
The loader reads the skill, sends it as a system prompt to the LLM, and executes.
"""

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


async def execute_skill(
    skill_name: str,
    task_prompt: str,
    context: dict = None,
    model: str = "fast",
) -> str:
    """
    Execute a skill by name.
    1. Find the skill
    2. Load its instructions
    3. Send to LLM with the task prompt
    4. Return the result
    """
    skill_path = find_skill(skill_name)
    if not skill_path:
        logger.warning(f"Skill '{skill_name}' not found in any skill directory")
        return ""

    skill_content = load_skill(skill_path)
    logger.info(f"Executing skill '{skill_name}' from {skill_path}")

    # Build context string
    ctx = ""
    if context:
        ctx = "\n\nCONTEXT:\n" + "\n".join(f"- {k}: {v}" for k, v in context.items())

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
                skills.append({
                    "name": name,
                    "description": desc,
                    "path": str(skill_file),
                    "source": str(skill_dir),
                })
            except Exception:
                continue
    return skills
