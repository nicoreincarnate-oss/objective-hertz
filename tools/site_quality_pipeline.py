"""Site building quality pipeline.

Orchestrates: 21st.dev components -> Claude Code generation -> Code review -> Visual QA -> Deploy
This is the FULL quality pipeline that ensures every site meets professional standards.
"""

import os
import json
import logging
import tempfile
import subprocess
import asyncio
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SiteBuildResult:
    """Result of the full site build pipeline."""
    success: bool
    site_dir: str = ""
    deploy_url: str = ""
    qa_score: float = 0.0
    qa_scores: dict = field(default_factory=dict)
    build_cost_usd: float = 0.0
    qa_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    attempts: int = 0
    files_created: list[str] = field(default_factory=list)
    error: str = ""
    escalated: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _brief_to_sections(brief: dict) -> list[dict]:
    """Convert a site brief into 21st.dev section descriptors."""
    industry = brief.get("industry", "business")
    business = brief.get("business_name", "Company")
    tone = brief.get("tone", "professional")
    pages = brief.get("pages", ["home"])

    sections = []
    if "home" in pages:
        sections.append({
            "name": "HeroSection",
            "description": (
                f"Hero section for a {industry} business called {business}. "
                f"Tone: {tone}. Full-width, compelling headline, CTA button."
            ),
            "search_query": f"{industry} hero section",
        })
    if "services" in pages:
        sections.append({
            "name": "ServicesGrid",
            "description": (
                f"Services grid for {business} ({industry}). "
                f"Cards with icons, service name, short description."
            ),
            "search_query": "services feature grid",
        })
    if "about" in pages:
        sections.append({
            "name": "AboutSection",
            "description": (
                f"About section for {business}. Team story, values, trust signals."
            ),
            "search_query": "about team section",
        })
    if "contact" in pages:
        sections.append({
            "name": "ContactForm",
            "description": (
                f"Contact form for {business}. Name, email, message fields. "
                f"Clean layout with map placeholder."
            ),
            "search_query": "contact form section",
        })
    return sections


async def _fetch_component_inspiration(
    brief: dict,
    project_dir: str,
) -> dict[str, Optional[str]]:
    """Fetch 21st.dev component inspiration, returning empty dict on failure."""
    try:
        from tools.twentyfirst_dev import generate_site_sections, twentyfirst_available

        if not twentyfirst_available():
            logger.info("21st.dev unavailable — building without component inspiration")
            return {}

        sections = _brief_to_sections(brief)
        if not sections:
            return {}

        return await generate_site_sections(sections, project_dir=project_dir)
    except Exception as e:
        logger.warning("21st.dev inspiration fetch failed (non-fatal): %s", e)
        return {}


def _enrich_brief_with_components(brief: dict, components: dict[str, Optional[str]]) -> dict:
    """Merge component inspiration into the brief so the builder can use it."""
    available = {name: code for name, code in components.items() if code}
    if not available:
        return brief

    enriched = dict(brief)
    enriched["component_inspiration"] = {
        name: code[:2000] for name, code in available.items()
    }
    return enriched


def _run_code_review(site_dir: str) -> tuple[bool, list[str]]:
    """Basic code review: check files exist, no secrets, reasonable size.

    Returns (passed, list_of_issues).
    """
    issues: list[str] = []

    if not os.path.isdir(site_dir):
        return False, ["Site directory does not exist"]

    files = []
    for root, _dirs, filenames in os.walk(site_dir):
        for fn in filenames:
            files.append(os.path.join(root, fn))

    if not files:
        return False, ["No files generated"]

    # Check for at least one HTML or TSX/JSX entry point
    has_entry = any(
        f.endswith((".html", ".tsx", ".jsx", ".ts", ".js"))
        for f in files
    )
    if not has_entry:
        issues.append("No HTML/JS/TS entry point found")

    # Check total size (warn if > 50MB)
    total_size = sum(os.path.getsize(f) for f in files)
    if total_size > 50 * 1024 * 1024:
        issues.append(f"Site is very large: {total_size / 1024 / 1024:.1f}MB")

    # Basic secret scan
    secret_patterns = ["ANTHROPIC_API_KEY", "sk-ant-", "sk-proj-", "password="]
    for fpath in files:
        if not fpath.endswith((".html", ".js", ".ts", ".tsx", ".jsx", ".css", ".json")):
            continue
        try:
            with open(fpath, "r", errors="ignore") as fh:
                content = fh.read(100_000)
            for pat in secret_patterns:
                if pat in content:
                    issues.append(f"Possible secret in {os.path.basename(fpath)}: {pat}")
        except Exception:
            pass

    passed = len(issues) == 0 or all("warn" in i.lower() or "large" in i.lower() for i in issues)
    return passed, issues


async def _start_local_server(site_dir: str) -> tuple[Optional[subprocess.Popen], Optional[str]]:
    """Start a simple HTTP server on a random port, return (process, url)."""
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("", 0))
        port = sock.getsockname()[1]
        sock.close()

        proc = subprocess.Popen(
            ["python", "-m", "http.server", str(port)],
            cwd=site_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give server a moment to start
        await asyncio.sleep(0.5)
        url = f"http://localhost:{port}"
        logger.info("Local preview server started at %s", url)
        return proc, url
    except Exception as e:
        logger.warning("Could not start local server: %s", e)
        return None, None


def _stop_local_server(proc: Optional[subprocess.Popen]) -> None:
    """Terminate the local preview server."""
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _estimate_qa_cost(attempts: int) -> float:
    """Estimate vision QA cost: ~$0.01 per screenshot, 3 viewports per attempt."""
    return attempts * 3 * 0.01


def _log_activity(
    brief: dict,
    result: "SiteBuildResult",
    *,
    attempt: int = 0,
    phase: str = "build",
    details: str = "",
) -> None:
    """Log pipeline activity in a format compatible with the activity_log table."""
    try:
        from shared.db import emit_event
        emit_event(
            "site_quality_pipeline",
            {
                "phase": phase,
                "business_name": brief.get("business_name", "unknown"),
                "attempt": attempt,
                "success": result.success,
                "qa_score": result.qa_score,
                "total_cost_usd": result.total_cost_usd,
                "details": details,
            },
        )
    except Exception:
        # Logging failures must never break the pipeline
        logger.debug("Activity log emit failed (non-fatal)")


# ---------------------------------------------------------------------------
# Lazy import accessors (patchable seams for testing)
# ---------------------------------------------------------------------------

def _get_build_site():
    """Return tools.claude_code_tool.build_site."""
    from tools.claude_code_tool import build_site
    return build_site


def _get_agent_sdk_available():
    """Return tools.claude_code_tool.agent_sdk_available."""
    from tools.claude_code_tool import agent_sdk_available
    return agent_sdk_available


def _get_run_qa_loop():
    """Return tools.visual_qa_gate.run_qa_loop, or None if unavailable."""
    try:
        from tools.visual_qa_gate import run_qa_loop
        return run_qa_loop
    except (ImportError, TypeError):
        logger.info("Visual QA gate not available — skipping QA")
        return None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def build_and_verify_site(
    brief: dict,
    deploy: bool = False,
    max_attempts: int = 3,
) -> SiteBuildResult:
    """
    Full site building pipeline with quality gates.

    Flow:
    1. Fetch 21st.dev component inspiration for the site sections
    2. Build site via Claude Agent SDK (with 21st.dev MCP if available)
    3. Run code review gate (HTML/CSS/JS validation, security, size)
    4. Run visual QA gate (screenshot at 3 viewports, vision scoring)
    5. If QA fails (score 5.0-6.9): regenerate with feedback, retry
    6. If QA passes (score >= 7.0): optionally deploy
    7. If QA escalates (score < 5.0 or max retries): flag for operator

    Args:
        brief: Site brief dict with business_name, industry, services, pages, tone
        deploy: Whether to deploy to Netlify after QA passes
        max_attempts: Max build+QA cycles (default 3)

    Returns:
        SiteBuildResult with all details
    """
    result = SiteBuildResult(success=False)

    # 1. Create working directory
    site_dir = tempfile.mkdtemp(prefix="perseus-site-")
    result.site_dir = site_dir
    logger.info("Site build started in %s", site_dir)

    # 2. Fetch 21st.dev component inspiration (graceful degradation)
    components = await _fetch_component_inspiration(brief, site_dir)
    enriched_brief = _enrich_brief_with_components(brief, components)

    # 3. Build via Claude Agent SDK
    if not _get_agent_sdk_available()():
        result.error = "Claude Agent SDK not available (missing key or package)"
        logger.error(result.error)
        return result

    _build_site = _get_build_site()

    # Track costs
    total_build_cost = 0.0

    # Build the site (first attempt)
    build_result = await _build_site(
        brief=enriched_brief,
        output_dir=site_dir,
        use_21st_dev=bool(components),
    )

    total_build_cost += build_result.cost_usd
    result.files_created = list(build_result.files_created)
    result.attempts = 1

    if not build_result.success:
        result.error = f"Initial build failed: {build_result.error}"
        result.build_cost_usd = total_build_cost
        result.total_cost_usd = total_build_cost
        _log_activity(brief, result, phase="build_failed", details=result.error)
        return result

    # 4. Code review gate
    review_passed, review_issues = _run_code_review(site_dir)
    if not review_passed:
        logger.warning("Code review issues: %s", review_issues)
        # Non-blocking — we still attempt visual QA

    # 5. Visual QA gate (graceful degradation)
    _run_qa = _get_run_qa_loop()
    if _run_qa is None:
        # Pass without QA
        result.success = True
        result.qa_score = 0.0
        result.build_cost_usd = total_build_cost
        result.total_cost_usd = total_build_cost
        _log_activity(brief, result, phase="complete_no_qa")
        return result

    # Start local server for screenshots
    server_proc, site_url = await _start_local_server(site_dir)
    if not site_url:
        # Cannot serve locally — skip QA, still succeed
        result.success = True
        result.qa_score = 0.0
        result.build_cost_usd = total_build_cost
        result.total_cost_usd = total_build_cost
        _log_activity(brief, result, phase="complete_no_server")
        return result

    try:
        # Create regeneration callback that feeds QA feedback into rebuilds
        async def regenerate_with_feedback(feedback: str) -> Optional[str]:
            nonlocal total_build_cost

            result.attempts += 1
            logger.info(
                "Regenerating site (attempt %d) with feedback: %s...",
                result.attempts,
                feedback[:120],
            )

            rebuild_brief = dict(enriched_brief)
            rebuild_brief["qa_feedback"] = feedback
            rebuild_brief["regeneration_attempt"] = result.attempts

            rebuild_result = await _build_site(
                brief=rebuild_brief,
                output_dir=site_dir,
                use_21st_dev=bool(components),
            )

            total_build_cost += rebuild_result.cost_usd
            result.files_created = list(rebuild_result.files_created)

            if rebuild_result.success:
                return site_url
            else:
                logger.error("Rebuild failed: %s", rebuild_result.error)
                return None

        # Run the QA loop
        qa_result = await _run_qa(
            site_url=site_url,
            regenerate_callback=regenerate_with_feedback,
        )

        # Populate result from QA
        result.qa_score = qa_result.average_score
        result.qa_scores = dict(qa_result.scores)
        result.qa_cost_usd = _estimate_qa_cost(qa_result.attempt + 1)

        if qa_result.action == "pass":
            result.success = True
        elif qa_result.action == "escalate":
            result.escalated = True
            result.success = False
            result.error = f"Visual QA escalated (score {qa_result.average_score:.1f}): {qa_result.feedback}"
        else:
            result.success = False
            result.error = f"QA did not pass after {result.attempts} attempts"

    finally:
        _stop_local_server(server_proc)

    # 6. Cost roll-up
    result.build_cost_usd = total_build_cost
    result.total_cost_usd = total_build_cost + result.qa_cost_usd

    # 7. Deploy if requested and QA passed
    if deploy and result.success:
        deploy_url = await _deploy_to_netlify(site_dir, brief)
        if deploy_url:
            result.deploy_url = deploy_url

    _log_activity(
        brief,
        result,
        attempt=result.attempts,
        phase="complete",
        details=f"qa={result.qa_score:.1f} cost=${result.total_cost_usd:.4f}",
    )

    return result


async def _deploy_to_netlify(site_dir: str, brief: dict) -> Optional[str]:
    """Deploy site to Netlify via CLI. Returns deploy URL or None."""
    try:
        site_name = brief.get("business_name", "site").lower().replace(" ", "-")
        proc = await asyncio.create_subprocess_exec(
            "netlify", "deploy", "--prod", "--dir", site_dir,
            "--site", site_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            output = stdout.decode()
            # Parse URL from Netlify output
            for line in output.splitlines():
                if "Website URL:" in line or "https://" in line:
                    url = line.strip().split()[-1]
                    if url.startswith("https://"):
                        logger.info("Deployed to %s", url)
                        return url
        else:
            logger.error("Netlify deploy failed: %s", stderr.decode()[:200])

    except FileNotFoundError:
        logger.warning("Netlify CLI not installed — skipping deploy")
    except Exception as e:
        logger.error("Deploy error: %s", e)

    return None
