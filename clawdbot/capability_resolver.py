"""
ClawdBot capability resolver — finds, installs, or builds missing capabilities.

When ClawdBot needs a tool it doesn't have, this module walks a resolution chain:
1. Check already-installed skills
2. Scan skill directories for partial matches
3. pip-install known packages (free, no API cost)
4. Ask a builder skill to create a wrapper (uses existing LLM budget)
5. Give up and alert Nico

Every step is recorded in agent_decisions for auditability.
"""

import asyncio
import logging
import sys
from datetime import datetime

from clawdbot.capabilities import (
    CAPABILITY_MAP,
    SKILL_INSTALL_DIR,
    SKILL_REGISTRIES,
    get_resolution_strategies,
)
from clawdbot.safety import vet_skill
from shared.comms import broadcast, record_decision
from shared.db import emit_event, get_config, set_config
from shared.skill_loader import execute_skill, find_skill

logger = logging.getLogger("perseus.clawdbot.resolver")


async def resolve_capability(need: str, context: dict | None = None) -> dict:
    """
    Try to resolve a missing capability.

    Returns:
        {"resolved": bool, "method": str, "skill_name": str, "details": str}
    """
    context = context or {}
    strategies = get_resolution_strategies(need)

    if not strategies:
        return await _give_up(need, "unknown_capability", f"No resolution strategies known for '{need}'", context)

    # Step 1: Check if any known skill is already installed
    for skill_name in strategies.get("skills", []):
        if find_skill(skill_name):
            result = {
                "resolved": True,
                "method": "already_installed",
                "skill_name": skill_name,
                "details": f"Skill '{skill_name}' is already installed.",
            }
            await _record(need, result, context)
            return result

    # Step 2: Spend gate — check if this capability has ongoing costs we can afford
    estimated_cost = strategies.get("estimated_monthly_cost", 0)
    if estimated_cost > 0:
        allowed = await _check_spend_gate(need, estimated_cost)
        if not allowed:
            return await _give_up(
                need,
                "spend_gate_blocked",
                f"Capability '{need}' costs ~${estimated_cost}/mo but expansion budget is exhausted.",
                context,
            )

    # Step 3: Search external skill registries (GitHub repos)
    for skill_name in strategies.get("skills", []):
        installed_from_registry = await _search_and_install_from_registry(skill_name)
        if installed_from_registry:
            result = {
                "resolved": True,
                "method": "registry_clone",
                "skill_name": skill_name,
                "details": f"Cloned and vetted skill '{skill_name}' from external registry.",
            }
            await _record(need, result, context)
            await _track_installation(need, "registry_clone", skill_name)
            return result

    # Step 4: Try pip-installing known packages
    for package in strategies.get("packages", []):
        installed = await _pip_install(package)
        if installed:
            post = strategies.get("post_install")
            if post:
                await _run_post_install(post)

            result = {
                "resolved": True,
                "method": "pip_install",
                "skill_name": "",
                "details": f"Installed Python package '{package}'.",
            }
            await _record(need, result, context)
            await _track_installation(need, "pip_install", package)
            return result

    # Step 4: Try to build via a builder skill
    builder_result = await _try_builder(need, strategies, context)
    if builder_result and builder_result.get("resolved"):
        return builder_result

    # Step 4: Give up
    return await _give_up(
        need,
        "all_strategies_exhausted",
        f"Tried skills {strategies.get('skills', [])}, packages {strategies.get('packages', [])}, builder — none worked.",
        context,
    )


async def _search_and_install_from_registry(skill_name: str) -> bool:
    """
    Search external skill registries for a skill, clone the repo if needed,
    and check if the skill is now available.

    Registries are git repos containing skill directories with SKILL.md files.
    We shallow-clone the whole repo into SKILL_INSTALL_DIR on first use,
    then subsequent lookups are instant (just check if the directory exists).
    """
    SKILL_INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    for registry in SKILL_REGISTRIES:
        repo_url = registry["url"]
        repo_name = registry["name"]
        clone_dir = SKILL_INSTALL_DIR / repo_name

        # Clone the registry repo if we haven't already
        if not clone_dir.exists():
            try:
                logger.info(f"Cloning skill registry '{repo_name}' from {repo_url}")
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--depth", "1", repo_url, str(clone_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode != 0:
                    logger.warning(f"Failed to clone {repo_url}: {stderr.decode()[:200]}")
                    continue
                logger.info(f"Cloned registry '{repo_name}' to {clone_dir}")
            except TimeoutError:
                logger.warning(f"Clone of {repo_url} timed out")
                continue
            except Exception as e:
                logger.warning(f"Clone of {repo_url} failed: {e}")
                continue
        else:
            # Pull latest changes periodically (non-blocking, best-effort)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", str(clone_dir), "pull", "--ff-only", "-q",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30)
            except Exception:
                pass  # Pull failure is non-critical

        # Now check if the skill exists in the cloned repo
        # find_skill() already scans ~/.openclaw/skills/ recursively,
        # so the cloned repo's skills are immediately discoverable.
        skill_path = find_skill(skill_name)
        if skill_path:
            # Safety gate: vet the skill before approving
            vet_result = await vet_skill(skill_path)
            if vet_result.passed:
                logger.info(f"Found and vetted skill '{skill_name}' in registry '{repo_name}'")
                return True
            else:
                logger.warning(
                    f"Skill '{skill_name}' from '{repo_name}' BLOCKED by safety gate: {vet_result.details[:200]}"
                )
                continue  # Try next registry

    return False


async def _pip_install(package: str) -> bool:
    """Install a Python package. Returns True on success."""
    try:
        logger.info(f"Attempting pip install: {package}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--quiet", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            logger.info(f"Successfully installed {package}")
            return True
        logger.warning(f"pip install {package} failed: {stderr.decode()[:200]}")
        return False
    except TimeoutError:
        logger.warning(f"pip install {package} timed out")
        return False
    except Exception as e:
        logger.warning(f"pip install {package} error: {e}")
        return False


async def _run_post_install(command: list[str]) -> bool:
    """Run a post-install command (e.g. playwright install chromium)."""
    try:
        logger.info(f"Running post-install: {' '.join(command)}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            logger.info(f"Post-install succeeded: {' '.join(command)}")
            return True
        logger.warning(f"Post-install failed: {stderr.decode()[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Post-install error: {e}")
        return False


async def _try_builder(need: str, strategies: dict, context: dict) -> dict | None:
    """Try to build a skill wrapper using an available builder skill."""
    builder_skills = CAPABILITY_MAP.get("code_builder", {}).get("skills", [])

    builder_skill = None
    for name in builder_skills:
        if find_skill(name):
            builder_skill = name
            break

    if not builder_skill:
        # No builder skill installed — create the skill directly via LLM
        return await _create_skill_directly(need, strategies, context)

    description = strategies.get("description", need)
    build_prompt = (
        f"Create a minimal skill that provides the '{need}' capability. "
        f"Description: {description}. "
        f"The skill should be a SKILL.md file that can be executed by ClawdBot. "
        f"Keep it simple — just enough to work."
    )

    try:
        logger.info(f"Asking builder skill '{builder_skill}' to create '{need}' capability")
        result_text = await execute_skill(
            builder_skill,
            task_prompt=build_prompt,
            context={"capability": need, **(context or {})},
        )

        if result_text and len(result_text) > 50:
            result = {
                "resolved": True,
                "method": "builder_skill",
                "skill_name": builder_skill,
                "details": f"Builder '{builder_skill}' created a wrapper for '{need}'.",
            }
            await _record(need, result, context)
            await _track_installation(need, "builder_skill", builder_skill)
            return result

    except Exception as e:
        logger.warning(f"Builder skill '{builder_skill}' failed for '{need}': {e}")

    return None


async def _create_skill_directly(need: str, strategies: dict, context: dict) -> dict | None:
    """Write a SKILL.md from scratch using GSD + Ralph Loop.

    Instead of a single LLM call, this decomposes skill creation into steps,
    executes each with validation, and retries on failure.
    """
    from shared.execution_loop import Step, TaskPlan, execute_plan

    description = strategies.get("description", need)
    skill_dir = SKILL_INSTALL_DIR / f"auto-{need}"
    skill_file = skill_dir / "SKILL.md"

    async def check_has_frontmatter(result: str) -> dict:
        if "name:" in result and "description:" in result:
            return {"passed": True}
        return {"passed": False, "error": "Missing YAML frontmatter (need name: and description: fields)"}

    async def check_has_instructions(result: str) -> dict:
        if len(result) > 200 and ("##" in result or "Instructions" in result.lower() or "how to" in result.lower()):
            return {"passed": True}
        return {"passed": False, "error": "Too short or missing instructions section"}

    async def check_passes_safety(result: str) -> dict:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(result)
        vr = await vet_skill(skill_file)
        if vr.passed:
            return {"passed": True}
        skill_file.unlink()
        try:
            skill_dir.rmdir()
        except OSError:
            pass
        return {"passed": False, "error": f"Safety gate: {vr.details[:200]}"}

    plan = TaskPlan(
        name=f"create_skill_{need}",
        description=f"Create SKILL.md for '{need}' capability",
        agent="clawdbot",
        max_retries=3,
        steps=[
            Step(
                name="write_frontmatter",
                prompt=(
                    f"Write YAML frontmatter for an OpenClaw SKILL.md file.\n"
                    f"Capability: {need}\nDescription: {description}\n\n"
                    f"Include: name, description, version (0.1.0), author (perseus-auto).\n"
                    f"Format: lines starting with 'key: value', no --- fences needed."
                ),
                model="fast",
                check=check_has_frontmatter,
            ),
            Step(
                name="write_full_skill",
                prompt=(
                    f"Write a complete SKILL.md file for an OpenClaw agent skill.\n\n"
                    f"Capability needed: {need}\nDescription: {description}\n\n"
                    f"A SKILL.md has:\n"
                    f"1. YAML frontmatter (name, description, version, author)\n"
                    f"2. Instructions section: step-by-step how to use this capability\n"
                    f"3. Examples of inputs and expected outputs\n"
                    f"4. Edge cases and error handling\n\n"
                    f"The skill is executed by sending its content as a system prompt to an LLM.\n"
                    f"Write ONLY the SKILL.md content. Start with the frontmatter."
                ),
                model="smart",
                max_tokens=4000,
                check=check_has_instructions,
            ),
            Step(
                name="safety_check",
                prompt="(safety check — no LLM call needed)",
                model="fast",
                check=check_passes_safety,
            ),
        ],
    )

    # Override step 3: it doesn't need an LLM call, just uses step 2's result
    # We'll run steps 1-2 via the loop, then manually run the safety check on step 2's output

    try:
        result = await execute_plan(plan)

        if not result["completed"]:
            # Check if we at least got past step 2 (have a skill file to vet)
            step_results = result.get("results", [])
            if len(step_results) >= 2 and step_results[1].get("passed"):
                # Step 2 passed but safety failed — already cleaned up in check
                pass
            logger.warning(f"Skill creation loop failed for '{need}': {result}")
            return None

        resolved = {
            "resolved": True,
            "method": "auto_created",
            "skill_name": f"auto-{need}",
            "details": f"Created and vetted SKILL.md for '{need}' capability.",
        }
        await _record(need, resolved, context)
        await _track_installation(need, "auto_created", f"auto-{need}")
        logger.info(f"Auto-created skill for '{need}' at {skill_file}")
        return resolved

    except Exception as e:
        logger.warning(f"Auto skill creation failed for '{need}': {e}")
        return None


async def _give_up(need: str, reason: str, details: str, context: dict) -> dict:
    """All resolution strategies exhausted. Alert Nico."""
    result = {
        "resolved": False,
        "method": "failed",
        "skill_name": "",
        "details": details,
    }
    await _record(need, result, context)
    await emit_event("capability_missing", {
        "capability": need,
        "reason": reason,
        "details": details,
    })
    await broadcast("urgent_alert", {
        "sender": "clawdbot",
        "message": f"ClawdBot needs '{need}' capability but can't resolve it: {details[:200]}",
    })
    return result


async def _record(need: str, result: dict, context: dict):
    """Record the resolution attempt in agent_decisions."""
    try:
        await record_decision(
            agent="clawdbot",
            decision_type="capability_resolution",
            context={"need": need, **(context or {})},
            decision=result,
            reasoning=result.get("details", ""),
        )
    except Exception as e:
        logger.debug(f"Failed to record capability resolution decision: {e}")


async def _track_installation(capability: str, method: str, target: str):
    """Track what ClawdBot has installed so it doesn't retry every cycle."""
    try:
        installed = await get_config("installed_capabilities", [])
        if not isinstance(installed, list):
            installed = []

        entry = {
            "capability": capability,
            "method": method,
            "target": target,
            "installed_at": datetime.now().isoformat(),
        }
        installed.append(entry)
        await set_config("installed_capabilities", installed)
    except Exception as e:
        logger.debug(f"Failed to track installation: {e}")


async def _check_spend_gate(capability: str, estimated_monthly_cost: float) -> bool:
    """Check if the expansion budget can absorb this capability's ongoing cost."""
    try:
        budget = float(await get_config("expansion_monthly_budget", 50) or 50)

        # Sum already-committed costs from installed capabilities
        installed = await get_config("installed_capabilities", [])
        committed = 0.0
        if isinstance(installed, list):
            for entry in installed:
                cap_name = entry.get("capability", "")
                strategies = CAPABILITY_MAP.get(cap_name, {})
                committed += strategies.get("estimated_monthly_cost", 0)

        remaining = budget - committed
        if estimated_monthly_cost > remaining:
            logger.warning(
                f"Spend gate: '{capability}' costs ~${estimated_monthly_cost}/mo but only ${remaining:.2f} expansion budget remaining"
            )
            await emit_event("expansion_spend_blocked", {
                "capability": capability,
                "estimated_cost": estimated_monthly_cost,
                "budget_remaining": remaining,
            })
            return False

        logger.info(f"Spend gate: '{capability}' ~${estimated_monthly_cost}/mo approved (${remaining:.2f} remaining)")
        return True

    except Exception as e:
        logger.debug(f"Spend gate check failed (allowing): {e}")
        return True  # Fail open


async def is_already_resolved(capability: str) -> bool:
    """Check if a capability was already resolved in a previous cycle."""
    # Check installed tracking
    try:
        installed = await get_config("installed_capabilities", [])
        if isinstance(installed, list):
            for entry in installed:
                if entry.get("capability") == capability:
                    return True
    except Exception:
        pass

    # Check if any known skill is available
    strategies = get_resolution_strategies(capability)
    if strategies:
        for skill_name in strategies.get("skills", []):
            if find_skill(skill_name):
                return True

    return False
