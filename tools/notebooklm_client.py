"""Google NotebookLM integration for Perseus.

Uses notebooklm-py (unofficial) for programmatic access to NotebookLM features:
- Create notebooks from pipeline data, learnings, and research
- Generate audio briefings (podcasts), infographics, slide decks, mind maps
- Bulk-import sources (URLs, PDFs, YouTube)
- Research automation with web search + auto-import

Install: pip install notebooklm-py
Auth: Requires Google account cookies (one-time browser auth)

Used by:
- Hermes: morning briefings as audio, weekly strategy as infographic/mind map
- ClawdBot: market research, client deliverables, skill synthesis
- Titan: training data analysis, campaign performance reports
"""

import asyncio
import json
import logging
from typing import Any

from tools.runtime_honesty import truth_payload

logger = logging.getLogger("perseus.tools.notebooklm")


def get_notebooklm_status() -> dict[str, Any]:
    """Check if NotebookLM is available."""
    try:
        import notebooklm  # noqa: F401
        return truth_payload(
            "live", "notebooklm", True,
            summary="notebooklm-py is installed and ready.",
            provider="notebooklm",
        )
    except ImportError:
        return truth_payload(
            "blocked", "notebooklm", False,
            summary="notebooklm-py not installed. Run: pip install notebooklm-py",
            provider="notebooklm",
        )


def _get_client():
    """Get a NotebookLM client instance."""
    from notebooklm import NotebookLM
    return NotebookLM()


async def create_notebook(
    title: str,
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    """Create a notebook with sources.

    Args:
        title: Notebook title
        sources: List of {"type": "url"|"text"|"youtube", "content": "..."}

    Returns:
        {"notebook_id": "...", "title": "...", "source_count": N}
    """
    try:
        client = _get_client()
        notebook = await asyncio.to_thread(client.create_notebook, title)
        notebook_id = notebook.id if hasattr(notebook, 'id') else str(notebook)

        added = 0
        for source in sources:
            try:
                source_type = source.get("type", "text")
                content = source.get("content", "")
                if not content:
                    continue

                if source_type == "url":
                    await asyncio.to_thread(client.add_source, notebook_id, url=content)
                elif source_type == "youtube":
                    await asyncio.to_thread(client.add_source, notebook_id, youtube_url=content)
                elif source_type == "text":
                    await asyncio.to_thread(client.add_source, notebook_id, text=content)
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add source: {e}")

        logger.info(f"Created notebook '{title}' with {added} sources")
        return {"notebook_id": notebook_id, "title": title, "source_count": added}

    except Exception as e:
        logger.error(f"NotebookLM create_notebook failed: {e}")
        return {"notebook_id": "", "error": str(e)}


async def generate_audio_overview(notebook_id: str) -> dict[str, Any]:
    """Generate an audio podcast overview from a notebook.

    Returns: {"audio_url": "...", "notebook_id": "..."}
    """
    try:
        client = _get_client()
        result = await asyncio.to_thread(client.generate_audio_overview, notebook_id)
        audio_url = result.url if hasattr(result, 'url') else str(result)
        logger.info(f"Generated audio overview for notebook {notebook_id}")
        return {"audio_url": audio_url, "notebook_id": notebook_id}
    except Exception as e:
        logger.error(f"Audio overview generation failed: {e}")
        return {"audio_url": "", "error": str(e)}


async def generate_infographic(notebook_id: str) -> dict[str, Any]:
    """Generate an infographic from a notebook.

    Returns: {"url": "...", "notebook_id": "..."}
    """
    try:
        client = _get_client()
        result = await asyncio.to_thread(client.generate, notebook_id, output_type="infographic")
        url = result.url if hasattr(result, 'url') else str(result)
        return {"url": url, "notebook_id": notebook_id, "type": "infographic"}
    except Exception as e:
        logger.error(f"Infographic generation failed: {e}")
        return {"url": "", "error": str(e)}


async def generate_slide_deck(notebook_id: str) -> dict[str, Any]:
    """Generate a slide deck from a notebook."""
    try:
        client = _get_client()
        result = await asyncio.to_thread(client.generate, notebook_id, output_type="slides")
        url = result.url if hasattr(result, 'url') else str(result)
        return {"url": url, "notebook_id": notebook_id, "type": "slides"}
    except Exception as e:
        logger.error(f"Slide deck generation failed: {e}")
        return {"url": "", "error": str(e)}


async def generate_mind_map(notebook_id: str) -> dict[str, Any]:
    """Generate a mind map from a notebook."""
    try:
        client = _get_client()
        result = await asyncio.to_thread(client.generate, notebook_id, output_type="mind_map")
        url = result.url if hasattr(result, 'url') else str(result)
        return {"url": url, "notebook_id": notebook_id, "type": "mind_map"}
    except Exception as e:
        logger.error(f"Mind map generation failed: {e}")
        return {"url": "", "error": str(e)}


async def generate_research_report(notebook_id: str) -> dict[str, Any]:
    """Generate a full research report from a notebook."""
    try:
        client = _get_client()
        result = await asyncio.to_thread(client.generate, notebook_id, output_type="report")
        content = result.content if hasattr(result, 'content') else str(result)
        return {"content": content, "notebook_id": notebook_id, "type": "report"}
    except Exception as e:
        logger.error(f"Research report generation failed: {e}")
        return {"content": "", "error": str(e)}


async def web_research(notebook_id: str, query: str) -> dict[str, Any]:
    """Run a web research query and auto-import results into the notebook."""
    try:
        client = _get_client()
        result = await asyncio.to_thread(client.research, notebook_id, query=query)
        sources_added = result.sources_added if hasattr(result, 'sources_added') else 0
        return {"query": query, "sources_added": sources_added, "notebook_id": notebook_id}
    except Exception as e:
        logger.error(f"Web research failed: {e}")
        return {"query": query, "error": str(e)}


# ── High-level Perseus workflows ──────────────────────────────────

async def create_briefing_notebook(
    pipeline_data: dict,
    learnings: list[str],
    metrics: dict,
) -> dict[str, Any]:
    """Create a notebook for Hermes morning/weekly briefings.

    Loads pipeline state, learnings, and metrics as sources,
    then generates audio overview + infographic.
    """
    sources = []

    # Pipeline state as text source
    if pipeline_data:
        sources.append({
            "type": "text",
            "content": f"PERSEUS PIPELINE STATE:\n{json.dumps(pipeline_data, indent=2, default=str)}",
        })

    # Learnings as text source
    if learnings:
        sources.append({
            "type": "text",
            "content": "TITAN LEARNINGS:\n" + "\n---\n".join(learnings),
        })

    # Metrics as text source
    if metrics:
        sources.append({
            "type": "text",
            "content": f"BUSINESS METRICS:\n{json.dumps(metrics, indent=2, default=str)}",
        })

    from datetime import date
    title = f"Perseus Briefing — {date.today().isoformat()}"
    notebook = await create_notebook(title, sources)

    if not notebook.get("notebook_id"):
        return notebook

    # Generate both audio and infographic
    audio = await generate_audio_overview(notebook["notebook_id"])
    infographic = await generate_infographic(notebook["notebook_id"])

    return {
        "notebook_id": notebook["notebook_id"],
        "title": title,
        "audio_url": audio.get("audio_url", ""),
        "infographic_url": infographic.get("url", ""),
    }


async def create_market_research_notebook(
    industry: str,
    region: str = "",
    competitor_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Create a notebook for ClawdBot market research.

    Bulk-imports competitor sites and industry URLs,
    runs web research, generates a structured report.
    """
    sources = []
    for url in (competitor_urls or []):
        sources.append({"type": "url", "content": url})

    title = f"Market Research — {industry}"
    if region:
        title += f" ({region})"

    notebook = await create_notebook(title, sources)
    if not notebook.get("notebook_id"):
        return notebook

    # Run web research queries
    queries = [
        f"{industry} businesses without websites {region}".strip(),
        f"{industry} website pricing {region}".strip(),
        f"{industry} digital marketing trends 2026",
    ]
    for query in queries:
        await web_research(notebook["notebook_id"], query)

    # Generate outputs
    report = await generate_research_report(notebook["notebook_id"])
    mind_map = await generate_mind_map(notebook["notebook_id"])

    return {
        "notebook_id": notebook["notebook_id"],
        "title": title,
        "report": report.get("content", ""),
        "mind_map_url": mind_map.get("url", ""),
    }


async def create_client_deliverable(
    business_name: str,
    industry: str,
    site_url: str,
    research_summary: str = "",
) -> dict[str, Any]:
    """Create a client-facing strategy guide notebook.

    Generates an infographic + slide deck the client receives
    alongside their website — upsell material for content packages.
    """
    sources = [
        {"type": "text", "content": (
            f"WEBSITE STRATEGY FOR {business_name}\n"
            f"Industry: {industry}\n"
            f"Website: {site_url}\n"
            f"Research: {research_summary}\n\n"
            "Cover: SEO recommendations, content calendar suggestions, "
            "social media strategy, analytics setup, and growth opportunities."
        )},
    ]
    if site_url:
        sources.append({"type": "url", "content": site_url})

    title = f"Strategy Guide — {business_name}"
    notebook = await create_notebook(title, sources)
    if not notebook.get("notebook_id"):
        return notebook

    infographic = await generate_infographic(notebook["notebook_id"])
    slides = await generate_slide_deck(notebook["notebook_id"])

    return {
        "notebook_id": notebook["notebook_id"],
        "title": title,
        "infographic_url": infographic.get("url", ""),
        "slides_url": slides.get("url", ""),
    }
