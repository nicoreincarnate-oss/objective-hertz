"""
Test Athena Studios site build using the ClawdBot site builder pipeline.

Feeds the brand identity, brand kit, and website brief into the same
system that builds client websites — to see what our own site would
look like when built by our own system.

Usage:
    PYTHONPATH=. python scripts/test-athena-site-build.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_brand_files() -> dict[str, str]:
    """Load all brand files as strings."""
    brand_dir = Path(__file__).parent.parent / "brand"
    files = {}
    for name in ["BRAND-IDENTITY.md", "BRAND-KIT.md", "WEBSITE-BRIEF.md", "VOICE-GUIDE.md"]:
        path = brand_dir / name
        if path.exists():
            files[name] = path.read_text()
    return files


def build_athena_lead() -> dict:
    """Construct a lead dict that represents Athena Studios itself."""
    brand = _load_brand_files()

    return {
        "id": "athena-studios-self",
        "business_name": "Athena Studios",
        "industry": "web design agency",
        "location": "El Sargento, Baja California Sur, Mexico",
        "website": None,
        "phone": "",
        "email": "hello@athenastudios.com",
        "tagline": "Your business online. Your customers calling.",
        "services": [
            "Custom Website Design — $299",
            "Landing Pages — $149",
            "Managed Hosting — $52/month",
            "24/7 AI Receptionist — $398/month",
            "Email Marketing — Custom quote",
        ],
        "unique_selling_points": [
            "Custom design, never templates — every site built for the specific business",
            "Transparent pricing — $299 for a 5-page professional website",
            "Faster than agencies — sites delivered in days, not weeks",
            "Cheaper than everyone — undercuts freelancers, agencies, and DIY builders",
            "Results-focused — designed to get calls and bookings, not just look pretty",
        ],
        "target_customers": "Local businesses worldwide — dentists, plumbers, restaurants, salons, contractors",
        "competitors": [
            "Wix/Squarespace ($16-45/mo, template only)",
            "Freelancers ($500-$2,500, inconsistent delivery)",
            "Agencies ($3,000-$10,000+, slow delivery)",
        ],
        "research_summary": brand.get("BRAND-IDENTITY.md", ""),
        "design_brief": brand.get("BRAND-KIT.md", ""),
        "copy_brief": brand.get("WEBSITE-BRIEF.md", ""),
        "voice_guide": brand.get("VOICE-GUIDE.md", ""),
        # Color palette from BRAND-KIT.md
        "brand_colors": {
            "primary": "#1B2A4A",
            "secondary": "#D4A853",
            "background": "#FFFFFF",
            "text": "#4A5568",
            "accent_light": "#F5E6C8",
            "surface": "#F7F8FA",
        },
        "brand_fonts": {
            "heading": "Inter",
            "body": "Inter",
            "heading_weight": "600",
            "body_weight": "400",
        },
        # Override design direction to match our brand kit
        "design_direction_override": {
            "name": "athena-brand",
            "style": "Professional, trustworthy, Athena wisdom aesthetic — navy + gold + white, clean geometry, subtle depth",
            "colors": "Athena Navy #1B2A4A + Gold #D4A853 + White, warm slate text",
            "fonts": "Inter (display 600/700) + Inter (body 400/500)",
            "layout": "Clean sections, floating cards with subtle shadow, mobile-first, clear CTAs",
            "animation": "Subtle card hover float (translateY -2px), section fade-in on scroll, button hover scale",
            "copy_angle": "Direct, benefit-led, transparent pricing, results-focused",
        },
        # Pages for multi-page build
        "pages": [
            {"name": "index", "title": "Home", "purpose": "Hero + value props + pricing preview + CTA"},
            {"name": "services", "title": "Services", "purpose": "Web design, hosting, marketing packages"},
            {"name": "portfolio", "title": "Portfolio", "purpose": "Example sites by industry"},
            {"name": "pricing", "title": "Pricing", "purpose": "3-tier pricing table + comparison + FAQ"},
            {"name": "about", "title": "About", "purpose": "Brand story + values + process"},
        ],
        # Lead score high so the system treats this as a priority build
        "lead_score": 95,
        "status": "demo_requested",
    }


async def main():
    """Run the site build for Athena Studios."""
    lead = build_athena_lead()

    print("=" * 60)
    print("ATHENA STUDIOS — Self-Build Test")
    print("=" * 60)
    print(f"Business: {lead['business_name']}")
    print(f"Industry: {lead['industry']}")
    print(f"Pages: {len(lead['pages'])}")
    print(f"Brand files loaded: {len([k for k in lead if lead[k]])}")
    print()

    # Check if we have API keys configured
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: No ANTHROPIC_API_KEY set.")
        print("The site builder needs Claude API access to generate HTML.")
        print()
        print("To run the full build:")
        print("  export ANTHROPIC_API_KEY=your-key-here")
        print("  PYTHONPATH=. python scripts/test-athena-site-build.py")
        print()
        print("For now, here's the lead dict that would be passed to the builder:")
        print()
        # Print a summary instead of the full dict (brand files are huge)
        summary = {k: v for k, v in lead.items() if k not in ("research_summary", "design_brief", "copy_brief", "voice_guide")}
        summary["research_summary"] = f"[BRAND-IDENTITY.md — {len(lead.get('research_summary', ''))} chars]"
        summary["design_brief"] = f"[BRAND-KIT.md — {len(lead.get('design_brief', ''))} chars]"
        summary["copy_brief"] = f"[WEBSITE-BRIEF.md — {len(lead.get('copy_brief', ''))} chars]"
        summary["voice_guide"] = f"[VOICE-GUIDE.md — {len(lead.get('voice_guide', ''))} chars]"
        print(json.dumps(summary, indent=2))
        return

    # Run the actual build
    from clawdbot.site_builder import build_full_site

    print("Starting 5-agent competitive build process...")
    print("This will generate multiple design variants, review them with Opus,")
    print("and synthesize the best elements into a final site.")
    print()

    url = await build_full_site(lead)

    if url:
        print()
        print("=" * 60)
        print(f"SITE DEPLOYED: {url}")
        print("=" * 60)
    else:
        print()
        print("Build completed but deployment failed (check logs).")
        print("The HTML was generated — check logs/ for the output.")


if __name__ == "__main__":
    asyncio.run(main())
