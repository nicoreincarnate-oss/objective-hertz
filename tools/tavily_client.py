"""Tavily search client for lead research enrichment.

Provides AI-optimized web search results for business validation,
competitive intelligence, and lead enrichment in the Titan pipeline.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def tavily_available() -> bool:
    """Check if Tavily is configured."""
    return bool(os.environ.get("TAVILY_API_KEY"))


async def search(
    query: str,
    search_depth: str = "basic",
    topic: str = "general",
    max_results: int = 5,
    include_domains: list[str] = None,
    exclude_domains: list[str] = None,
) -> list[dict]:
    """
    Search the web using Tavily's AI-optimized search.

    Args:
        query: Search query string
        search_depth: "basic" (1 credit) or "advanced" (2 credits)
        topic: "general" or "news"
        max_results: Number of results (1-10)
        include_domains: Only search these domains
        exclude_domains: Exclude these domains

    Returns:
        List of dicts with: title, url, content, score, raw_content
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set, skipping Tavily search")
        return []

    try:
        from tavily import AsyncTavilyClient
    except ImportError:
        logger.warning("tavily-python not installed. Run: pip install tavily-python")
        return []

    try:
        client = AsyncTavilyClient(api_key=api_key)

        kwargs = {
            "query": query,
            "search_depth": search_depth,
            "topic": topic,
            "max_results": max_results,
        }
        if include_domains:
            kwargs["include_domains"] = include_domains
        if exclude_domains:
            kwargs["exclude_domains"] = exclude_domains

        response = await client.search(**kwargs)

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0.0),
                "raw_content": item.get("raw_content", ""),
            })

        logger.info(f"Tavily: {len(results)} results for '{query}'")
        return results

    except Exception as e:
        logger.error(f"Tavily search error: {e}")
        return []


async def research_business(
    business_name: str,
    location: str = "",
    aspects: list[str] = None,
) -> dict:
    """
    Research a business for lead enrichment.
    Searches for key aspects: website, reviews, competitors, social media.

    Args:
        business_name: Name of the business
        location: City/state for local context
        aspects: What to research (defaults to standard set)

    Returns:
        Dict with enrichment data: website_info, reviews_summary,
        competitors, social_media, has_website, overall_summary
    """
    if aspects is None:
        aspects = ["website quality", "reviews", "competitors", "social media"]

    location_str = f" {location}" if location else ""
    enrichment = {
        "business_name": business_name,
        "location": location,
        "website_info": "",
        "reviews_summary": "",
        "competitors": [],
        "social_media": [],
        "has_website": False,
        "overall_summary": "",
        "sources": [],
    }

    # Search for the business
    query = f'"{business_name}"{location_str}'
    results = await search(query, max_results=5)

    if not results:
        return enrichment

    # Analyze results
    for result in results:
        enrichment["sources"].append(result["url"])
        content_lower = result["content"].lower()

        # Check if they have a website (not just directory listings)
        if business_name.lower() in result["url"].lower():
            enrichment["has_website"] = True
            enrichment["website_info"] = result["content"][:500]

        # Look for review mentions
        if any(kw in content_lower for kw in ["review", "rating", "stars", "yelp", "google reviews"]):
            enrichment["reviews_summary"] += result["content"][:300] + " "

        # Look for social media
        for platform in ["facebook.com", "instagram.com", "twitter.com", "linkedin.com", "tiktok.com"]:
            if platform in result["url"]:
                enrichment["social_media"].append(result["url"])

    # Search for competitors
    competitor_query = f'{business_name}{location_str} alternatives competitors similar'
    competitor_results = await search(competitor_query, max_results=3)

    for result in competitor_results:
        if business_name.lower() not in result["title"].lower():
            enrichment["competitors"].append({
                "name": result["title"],
                "url": result["url"],
                "snippet": result["content"][:200],
            })

    # Build overall summary
    has_site = "has a website" if enrichment["has_website"] else "no website found"
    review_info = enrichment["reviews_summary"][:200] if enrichment["reviews_summary"] else "no reviews found"
    num_competitors = len(enrichment["competitors"])
    social_count = len(enrichment["social_media"])

    enrichment["overall_summary"] = (
        f"{business_name} ({has_site}, {social_count} social profiles, "
        f"{num_competitors} competitors found). Reviews: {review_info}"
    )

    enrichment["reviews_summary"] = enrichment["reviews_summary"].strip()[:500]

    return enrichment
