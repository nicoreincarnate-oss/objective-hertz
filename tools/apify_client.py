"""Apify lead generation client for Perseus pipeline."""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default actors
DEFAULT_DISCOVERY_ACTOR = "compass/crawler-google-places"
ENRICHMENT_ACTOR = "solutionssmart/local-business-lead-finder"


async def apify_available() -> bool:
    """Check if Apify is configured."""
    return bool(os.environ.get("APIFY_API_TOKEN"))


async def discover_leads(
    query: str,
    location: str,
    max_results: int = 50,
    actor_id: str = DEFAULT_DISCOVERY_ACTOR,
) -> list[dict]:
    """
    Discover business leads via Apify Google Maps scraper.

    Returns list of dicts with: name, phone, email, website, address,
    city, state, category, rating, reviews_count, latitude, longitude
    """
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        logger.warning("APIFY_API_TOKEN not set, skipping Apify discovery")
        return []

    try:
        from apify_client import ApifyClientAsync
    except ImportError:
        logger.warning("apify-client not installed, run: pip install apify-client")
        return []

    client = ApifyClientAsync(token)
    try:
        search_string = f"{query} in {location}" if location else query

        run_input = {
            "searchStringsArray": [search_string],
            "maxCrawledPlacesPerSearch": max_results,
            "language": "en",
            "deeperCityScrape": False,
        }

        logger.info(f"Apify: Running {actor_id} for '{search_string}' (max {max_results})")

        run = await client.actor(actor_id).call(run_input=run_input)

        if not run or run.get("status") != "SUCCEEDED":
            logger.error(f"Apify run failed: {run}")
            return []

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            logger.error("Apify run has no dataset")
            return []

        result = await client.dataset(dataset_id).list_items()
        items = result.items if result else []

        leads = _normalize_leads(items)

        logger.info(f"Apify: Found {len(leads)} leads for '{search_string}'")
        return leads

    except Exception as e:
        logger.error(f"Apify discovery error: {e}")
        return []
    finally:
        if hasattr(client, "close"):
            await client.close()


def _normalize_leads(items: list[dict]) -> list[dict]:
    """Normalize raw Apify items to Perseus lead format."""
    leads = []
    for item in items:
        location_data = item.get("location") or {}
        lead = {
            "name": item.get("title", ""),
            "phone": item.get("phone", ""),
            "email": "",  # Google Maps doesn't provide email directly
            "website": item.get("website", ""),
            "address": item.get("address", ""),
            "city": item.get("city", ""),
            "state": item.get("state", ""),
            "category": item.get("categoryName", ""),
            "rating": item.get("totalScore"),
            "reviews_count": item.get("reviewsCount", 0),
            "latitude": location_data.get("lat"),
            "longitude": location_data.get("lng"),
            "source": "apify",
            "source_url": item.get("url", ""),
            "place_id": item.get("placeId", ""),
        }
        if lead["name"]:  # Skip empty entries
            leads.append(lead)
    return leads


async def enrich_lead_emails(
    leads: list[dict],
    actor_id: str = ENRICHMENT_ACTOR,
) -> list[dict]:
    """
    Enrich leads with email addresses by crawling their websites.
    Uses solutionssmart/local-business-lead-finder.
    """
    token = os.environ.get("APIFY_API_TOKEN")
    if not token or not leads:
        return leads

    try:
        from apify_client import ApifyClientAsync
    except ImportError:
        return leads

    # Extract websites for enrichment
    websites = [lead["website"] for lead in leads if lead.get("website")]
    if not websites:
        return leads

    client = ApifyClientAsync(token)
    try:
        run = await client.actor(actor_id).call(run_input={
            "startUrls": [{"url": w} for w in websites[:50]],  # Cap at 50 for cost
        })

        if run and run.get("status") == "SUCCEEDED":
            dataset_id = run.get("defaultDatasetId")
            result = await client.dataset(dataset_id).list_items()

            # Build website -> email lookup
            email_lookup: dict[str, str] = {}
            for item in (result.items if result else []):
                url = item.get("website") or item.get("url", "")
                emails = item.get("emails", [])
                if url and emails:
                    email_lookup[url] = emails[0]  # Take first email

            # Merge emails back into leads
            for lead in leads:
                if lead.get("website") in email_lookup:
                    lead["email"] = email_lookup[lead["website"]]

        return leads
    except Exception as e:
        logger.error(f"Apify enrichment error: {e}")
        return leads
    finally:
        if hasattr(client, "close"):
            await client.close()
