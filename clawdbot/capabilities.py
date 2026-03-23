"""
Known capability map for ClawdBot self-equipping.

Each entry describes what skills or packages can fulfill a capability need,
any post-install steps required, and external repos where skills can be found.

ClawdBot reads this before trying to resolve a missing capability.
New entries can be added by the expansion system or manually.
"""

from pathlib import Path

# Where to install skills cloned from external repos.
# Uses ~/.openclaw/skills/ so find_skill() picks them up automatically.
SKILL_INSTALL_DIR = Path.home() / ".openclaw" / "skills"

# External skill registries ClawdBot can search and clone from.
SKILL_REGISTRIES: list[dict] = [
    {
        "name": "awesome-openclaw-skills",
        "url": "https://github.com/VoltAgent/awesome-openclaw-skills.git",
        "description": "Curated community skill collection for OpenClaw agents (5,400+ skills).",
    },
    {
        "name": "droidclaw",
        "url": "https://github.com/unitedbyai/droidclaw.git",
        "description": "Android AI automation — turn old phones into robot hands via ADB.",
    },
    {
        "name": "swarmclaw",
        "url": "https://github.com/swarmclawai/swarmclaw.git",
        "description": "Multi-agent orchestration: delegation, memory, skill sharing, connectors.",
    },
]

CAPABILITY_MAP: dict[str, dict] = {

    # ── Core "powerhouse" evolution ──────────────────────────────────

    "capability_evolver": {
        "skills": ["capability-evolver", "prompt-tuner", "auto-optimizer"],
        "packages": [],
        "description": "Background trainer that watches agent performance and auto-tunes prompts/strategies over time.",
    },
    "self_improving": {
        "skills": ["self-improving-agent", "agent-memory", "adaptive-agent"],
        "packages": [],
        "description": "Persistent structured memory with feedback loop so the system gets better instead of repeating mistakes.",
    },
    "skill_creator": {
        "skills": ["skill-creator", "skill-generator", "skill-factory"],
        "packages": [],
        "description": "Generate new skills from natural language descriptions.",
    },
    "skill_vetter": {
        "skills": ["skill-vetter", "skill-validator", "skill-safety-check"],
        "packages": [],
        "description": "Vet and safety-check skills before they go live.",
    },

    # ── Multi-agent orchestration and teams ──────────────────────────

    "agent_orchestrator": {
        "skills": ["agent-orchestrator", "agent-task-manager", "task-decomposer"],
        "packages": [],
        "description": "Meta-agent that decomposes tasks, routes subtasks to sub-agents, and tracks state.",
    },
    "agent_network": {
        "skills": ["agent-weave", "agent-network", "agent-cluster"],
        "packages": [],
        "description": "Coordinate clusters of worker agents in parallel for 3-5x throughput on complex tasks.",
    },
    "agent_optimizer": {
        "skills": ["agent-orchestration-multi-agent-optimize", "agent-load-balancer", "agent-router"],
        "packages": [],
        "description": "Observe multi-agent setup and adjust task routing, load-balancing, and model selection.",
    },

    # ── World access and automation ──────────────────────────────────

    "browser": {
        "skills": ["agent-browser", "browser-use", "playwright-browser", "puppeteer"],
        "packages": ["playwright"],
        "post_install": ["playwright", "install", "chromium"],
        "description": "Full browser automation: multi-tab, scraping, forms, screenshots. Operate on arbitrary SaaS and the open web.",
    },
    "web_search": {
        "skills": ["tavily-search", "exa-search", "openclaw-free-web-search", "firecrawl-search", "serp-api"],
        "packages": ["tavily-python"],
        "description": "Agent-optimized web search that feeds high-quality context into the orchestration layer.",
    },
    "google_workspace": {
        "skills": ["gog-workspace", "google-workspace", "gmail-agent", "gcal-agent", "gdocs-agent"],
        "packages": [],
        "description": "Glue to email, calendar, docs. Lets the orchestrator interact with Google Workspace.",
    },
    "n8n_workflow": {
        "skills": ["n8n-workflow", "n8n-trigger", "n8n-automation"],
        "packages": [],
        "description": "Kick off n8n flows for anything repetitive. External automation glue.",
    },
    "scraper": {
        "skills": ["smart-web-scraper", "firecrawl-scrape", "apify-web-scraper"],
        "packages": [],
        "description": "Extract structured data from web pages.",
    },

    # ── Safety, governance, and meta-control ─────────────────────────

    "agent_sentinel": {
        "skills": ["agent-sentinel", "security-audit", "agent-governor"],
        "packages": [],
        "description": "Governor that inspects skills and running tasks, blocks sketchy toolchains, trips circuit-breaker on weird behavior or spend.",
    },
    "mission_control": {
        "skills": ["mission-control", "proactive-agent", "ops-dashboard"],
        "packages": [],
        "description": "Morning briefings, status dashboards, and proactive task suggestions. Ops layer, not a passive chatbot.",
    },

    # ── Revenue pipeline specific ────────────────────────────────────

    "email_finder": {
        "skills": ["email-finder", "hunter-io", "snov-io", "email-extractor"],
        "packages": [],
        "description": "Find contact emails for leads.",
    },
    "phone": {
        "skills": ["twilio-sms", "voip-caller", "twilio-voice"],
        "packages": ["twilio"],
        "description": "Make calls or send SMS.",
    },
    "code_builder": {
        "skills": ["claude-code", "codex-collab", "openclaw-coder", "cursor-agent"],
        "packages": [],
        "description": "Build new skills, scripts, or tools autonomously.",
    },
    "lead_generation": {
        "skills": ["apify-lead-generation", "outbound-prospecting", "google-maps-scraper"],
        "packages": [],
        "description": "Find businesses and contact info at scale.",
    },

    # ── Claw ecosystem: world access + mobile + voice ─────────────────

    "voice_call": {
        "skills": ["clawdtalk", "telnyx-voice", "twilio-voice"],
        "packages": ["telnyx"],
        "description": "Give the agent a real phone number for voice calls, SMS, and WhatsApp via ClawdTalk/Telnyx.",
        "estimated_monthly_cost": 15.0,
    },
    "voice_synth": {
        "skills": ["elevenlabs-agent", "tts-agent", "voice-clone"],
        "packages": ["elevenlabs"],
        "description": "Text-to-speech and voice synthesis for outbound calls or voice messages.",
        "estimated_monthly_cost": 5.0,
    },
    "android_automation": {
        "skills": ["droidclaw", "android-agent", "adb-automation"],
        "packages": ["adbutils"],
        "description": "DroidClaw: use an Android phone as robot hands — tap, type, scroll, screenshot via ADB. 28 built-in actions.",
    },
    "mobile_agent": {
        "skills": ["zeroclaw", "zeroclaw-agent"],
        "packages": [],
        "description": "ZeroClaw: ultra-lightweight Rust agent runtime (<5MB RAM). Runs on $10 hardware, Android, Raspberry Pi.",
    },
    "swarm_orchestrator": {
        "skills": ["swarmclaw", "agent-swarm", "swarm-control"],
        "packages": [],
        "description": "SwarmClaw: multi-agent control plane with delegation, memory sharing, skill library, and connectors.",
    },
    "sms_outreach": {
        "skills": ["telnyx-sms", "twilio-sms", "sms-sender"],
        "packages": ["telnyx"],
        "description": "Send SMS follow-ups as an alternative channel to email. Uses ClawdTalk or Twilio.",
        "estimated_monthly_cost": 10.0,
    },
    "whatsapp": {
        "skills": ["whatsapp-business", "telnyx-whatsapp", "whatsapp-agent"],
        "packages": [],
        "description": "WhatsApp Business messaging for prospect outreach and customer communication.",
        "estimated_monthly_cost": 15.0,
    },
    "screenshot_qa": {
        "skills": ["screenshot-agent", "visual-qa", "page-screenshot"],
        "packages": ["playwright"],
        "post_install": ["playwright", "install", "chromium"],
        "description": "Take screenshots of pages and visually verify content, layout, and branding.",
    },

    # ── Creative & visual generation ──────────────────────────────────

    "image_generation": {
        "skills": ["recraft-image", "image-gen", "dall-e"],
        "packages": [],
        "description": (
            "Recraft AI: production-grade image and vector generation via API. "
            "Logos, icons, branded visuals, social media graphics, email headers. "
            "V4 model, $0.01/image. OpenAI-compatible SDK."
        ),
        "api_env": "RECRAFT_API_KEY",
        "estimated_monthly_cost": 1.0,
    },
    "notebooklm": {
        "skills": ["notebooklm-research", "notebooklm-briefing"],
        "packages": ["notebooklm-py"],
        "description": (
            "Google NotebookLM: create notebooks from sources, generate audio podcasts, "
            "infographics, slide decks, mind maps, research reports. Free with Google account."
        ),
    },
    "site_builder_alt": {
        "skills": ["loki-build", "site-builder"],
        "packages": [],
        "description": "Loki.Build: alternative AI website builder for landing pages. Fallback when v0.dev is down.",
    },
}


def get_resolution_strategies(capability: str) -> dict | None:
    """Return the resolution strategies for a capability, or None if unknown."""
    return CAPABILITY_MAP.get(capability)


def all_capability_names() -> list[str]:
    """Return all known capability names."""
    return list(CAPABILITY_MAP.keys())
