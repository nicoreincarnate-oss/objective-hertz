#!/usr/bin/env python3
"""
OpenJarvis Daily Watch — monitors open-jarvis/OpenJarvis repo
and sends Telegram analysis to Nico re: Perseus integration potential.

Runs as a scheduled task. Uses GitHub CLI (gh) for repo data
and Telegram Bot API for notifications.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

# ── Config ──────────────────────────────────────────────────────────
REPO = "open-jarvis/OpenJarvis"
WATCH_START = "2026-03-22"
WATCH_DAYS = 30

# Load from env or .env
ROOT = Path(__file__).resolve().parent.parent

def _load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set — openjarvis_watch cannot start")
if not CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID is not set — openjarvis_watch cannot start")


def gh_api(endpoint: str) -> dict | list:
    """Call GitHub API via gh CLI."""
    result = subprocess.run(
        ["gh", "api", endpoint, "--cache", "1h"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr}")
    return json.loads(result.stdout)


def get_repo_stats() -> dict:
    """Fetch core repo metrics."""
    repo = gh_api(f"repos/{REPO}")
    return {
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "open_issues": repo["open_issues_count"],
        "pushed_at": repo["pushed_at"],
        "size_kb": repo["size"],
    }


def get_recent_commits(days: int = 1) -> list[dict]:
    """Fetch commits from the last N days."""
    since = (datetime.now(tz=__import__('zoneinfo').ZoneInfo("UTC")) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = gh_api(f"repos/{REPO}/commits?since={since}&per_page=50")
    return [
        {
            "sha": c["sha"][:7],
            "msg": c["commit"]["message"].split("\n")[0][:80],
            "author": c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"][:10],
        }
        for c in commits
    ]


def get_recent_releases() -> list[dict]:
    """Fetch recent releases."""
    releases = gh_api(f"repos/{REPO}/releases?per_page=5")
    return [
        {
            "tag": r["tag_name"],
            "name": r["name"],
            "date": r["published_at"][:10] if r["published_at"] else "draft",
            "prerelease": r["prerelease"],
        }
        for r in releases
    ]


def get_open_prs() -> list[dict]:
    """Fetch open PRs."""
    prs = gh_api(f"repos/{REPO}/pulls?state=open&per_page=10")
    return [
        {
            "number": p["number"],
            "title": p["title"][:60],
            "user": p["user"]["login"],
        }
        for p in prs
    ]


def assess_perseus_relevance(stats, commits, releases, prs) -> dict:
    """Evaluate whether OpenJarvis components are ready for Perseus integration."""
    # Track key signals
    signals = []
    score = 0  # 0-10 integration readiness

    # Activity signal
    if len(commits) > 5:
        signals.append(f"High activity: {len(commits)} commits in 24h")
        score += 1  # active = good but also unstable API
    elif len(commits) > 0:
        signals.append(f"Moderate activity: {len(commits)} commits in 24h")
        score += 2  # stable pace is better for integration

    # Star velocity (proxy for community validation)
    if stats["stars"] > 5000:
        signals.append(f"Strong adoption: {stats['stars']} stars")
        score += 3
    elif stats["stars"] > 2000:
        signals.append(f"Growing adoption: {stats['stars']} stars")
        score += 2
    else:
        signals.append(f"Early stage: {stats['stars']} stars")
        score += 1

    # Release stability
    stable_releases = [r for r in releases if not r["prerelease"]]
    if stable_releases:
        signals.append(f"Has stable release: {stable_releases[0]['tag']}")
        score += 3
    elif releases:
        signals.append(f"Pre-release only: {releases[0]['tag']}")
        score += 1
    else:
        signals.append("No releases yet")

    # Specific Perseus-relevant areas to watch
    areas = []
    commit_text = " ".join(c["msg"].lower() for c in commits)
    for keyword, area in [
        ("engine", "Inference engine updates"),
        ("ollama", "Ollama integration changes"),
        ("tool", "Tool system evolution"),
        ("mcp", "MCP protocol work"),
        ("daemon", "Daemon architecture"),
        ("scheduler", "Scheduler changes"),
        ("telegram", "Telegram integration"),
        ("agent", "Agent framework updates"),
        ("local", "Local-first improvements"),
        ("eval", "Eval framework updates"),
    ]:
        if keyword in commit_text:
            areas.append(area)

    recommendation = "WAIT"
    if score >= 7:
        recommendation = "EVALUATE"
    elif score >= 5:
        recommendation = "WATCH CLOSELY"

    return {
        "score": score,
        "recommendation": recommendation,
        "signals": signals,
        "relevant_areas": areas,
    }


def build_report(stats, commits, releases, prs, assessment) -> str:
    """Build the Telegram message."""
    day_num = (datetime.now() - datetime.strptime(WATCH_START, "%Y-%m-%d")).days + 1
    remaining = WATCH_DAYS - day_num

    msg = f"🔭 *OpenJarvis Watch — Day {day_num}/{WATCH_DAYS}*\n\n"

    # Stats
    msg += f"⭐ {stats['stars']} stars | 🍴 {stats['forks']} forks\n"
    msg += f"📝 {len(commits)} commits (24h) | 🔀 {len(prs)} open PRs\n\n"

    # Assessment
    msg += f"*Perseus Integration: {assessment['recommendation']}* ({assessment['score']}/10)\n"
    for s in assessment["signals"]:
        msg += f"  • {s}\n"

    # Relevant changes
    if assessment["relevant_areas"]:
        msg += "\n*Relevant to Perseus:*\n"
        for area in assessment["relevant_areas"]:
            msg += f"  → {area}\n"

    # Notable commits
    if commits:
        msg += "\n*Key commits:*\n"
        for c in commits[:5]:
            msg += f"  `{c['sha']}` {c['msg']}\n"

    # New releases
    if releases:
        latest = releases[0]
        msg += f"\n*Latest release:* {latest['tag']} ({latest['date']})"
        if latest["prerelease"]:
            msg += " (pre-release)"
        msg += "\n"

    if remaining > 0:
        msg += f"\n_{remaining} days remaining in watch period_"
    else:
        msg += "\n⏰ *Watch period complete — final report*"

    return msg


def send_telegram(text: str):
    """Send message via Telegram Bot API."""
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        sys.exit(1)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=15)
    result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    print(f"Message sent to chat {CHAT_ID}")


def main():
    print(f"OpenJarvis Watch — {datetime.now().isoformat()}")

    # Check watch period
    start = datetime.strptime(WATCH_START, "%Y-%m-%d")
    if (datetime.now() - start).days > WATCH_DAYS:
        print("Watch period expired. Exiting.")
        return

    stats = get_repo_stats()
    commits = get_recent_commits(days=1)
    releases = get_recent_releases()
    prs = get_open_prs()
    assessment = assess_perseus_relevance(stats, commits, releases, prs)

    report = build_report(stats, commits, releases, prs, assessment)
    print(report)
    send_telegram(report)


if __name__ == "__main__":
    main()
