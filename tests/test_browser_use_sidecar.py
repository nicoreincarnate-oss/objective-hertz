from clawdbot.browser_use_sidecar import _browser_use_task_prompt


def test_browser_use_prompt_switches_to_research_mode():
    prompt = _browser_use_task_prompt(
        {
            "objective": "Research the best AI browser automation frameworks and report back",
            "deliverable": "short comparison memo",
            "success_criteria": "include sources and tradeoffs",
        }
    )

    assert "research mode" in prompt.lower()
    assert "short comparison memo" in prompt
    assert "include sources and tradeoffs" in prompt


def test_browser_use_prompt_stays_simple_for_non_research_tasks():
    prompt = _browser_use_task_prompt(
        {
            "objective": "Open the site and click the login button",
        }
    )

    assert "research mode" not in prompt.lower()
    assert "live browser-use session server" in prompt
