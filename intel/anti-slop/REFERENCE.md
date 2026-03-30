# Anti-Slop Tools — Reference

**Key Repositories:**
- https://github.com/DataWhisker/anti-slop-skill (Claude Code skill, 60+ rules)
- https://github.com/peakoss/anti-slop (GitHub Action, 31 checks)
- https://github.com/peteromallet/desloppify (Agent harness for code quality)
- https://github.com/jalaalrd/anti-ai-slop-writing (Writing anti-slop)

---

## Why This Matters for Objective Hertz

Titan composes cold emails at `titan/pipeline/email_compose.py`. If they read like AI slop, open rates tank and the revenue model breaks. ClawdBot generates site copy. Both need quality gates. Currently there's no anti-slop check between composition and delivery in either daemon.

**Direct application:** Add a quality gate between `email_compose` and `email_send` stages. After Titan composes an email, route it through a separate LLM call that scores it on naturalness. If it fails, recompose.

---

## Tool 1: Anti-Slop Skill (DataWhisker)

### Architecture
A Claude Code skill that catches AI-generated slop, security hazards, and low-quality code before it ships.

### 9 Check Categories with 60+ Rules

**1. Security Hazards**
- Hardcoded API keys and secrets (25+ regex patterns)
- AWS keys: `AKIA[0-9A-Z]{16}`
- Stripe: `sk_live_[a-zA-Z0-9]{24,}`
- Anthropic: `sk-ant-[a-zA-Z0-9-]+`
- Generic secrets: `(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"][^'\"]{8,}`
- Exposed credentials in configs
- Insecure HTTP endpoints
- SQL injection vulnerabilities
- Missing input validation

**2. AI Slop Patterns (Code)**
- Excessive TODO/FIXME without context
- Copy-pasted boilerplate
- Overly verbose variable naming
- Unnecessary type assertions
- Dead code blocks
- Placeholder implementations that look complete

**3. AI Slop Patterns (Writing)**
- Hedging language: "It's important to note that", "It's worth mentioning"
- Corporate buzzwords: "leverage", "synergize", "paradigm shift"
- Unnecessary qualifiers: "essentially", "fundamentally", "basically"
- Filler transitions: "Furthermore", "Moreover", "Additionally" (when overused)
- AI tells: "As an AI", "I don't have personal experience"

**4. Error Handling**
- Empty catch blocks
- Generic error swallowing
- Missing error propagation
- Silent failures

**5. Performance**
- N+1 query patterns
- Unnecessary re-renders
- Missing memoization
- Synchronous operations that should be async

**6. Architecture**
- God classes/functions
- Circular dependencies
- Tight coupling
- Missing abstraction layers

**7. Testing**
- Tests that always pass
- Missing edge cases
- Mocked everything (testing mocks, not code)
- No assertion messages

**8. Documentation**
- Comments that restate the code
- Missing API documentation
- Outdated comments
- Missing parameter descriptions

**9. Cross-Language Leakage**
- JavaScript patterns in Python (camelCase in snake_case codebase)
- Python patterns in JavaScript
- 17 cross-language detection patterns

### The Subagent Delegation Pattern (Key Innovation)

When you ask Claude to review code it just wrote, it has inherent bias. The anti-slop skill automatically delegates review to a **fresh sub-agent with zero conversation history** and no design rationale.

```
Agent A writes code → Anti-slop triggers
→ Spawns Agent B (fresh context, no history)
→ Agent B reviews with cold eyes
→ Findings returned to Agent A
```

**Why this matters:** The agent that composed Titan's email shouldn't be the one reviewing it. A fresh agent with no context about the composition process will catch slop patterns the composer is blind to.

---

## Tool 2: Anti-Slop GitHub Action (peakoss)

### 31 Checks Across 7 Categories

Built from patterns identified across **130+ manually reviewed AI slop PRs** submitted to large open-source projects.

**PR Branch Checks:**
- Branch name follows convention
- Not pushing to protected branches
- Branch is up to date with base

**PR Title Checks:**
- Not generic ("Update files", "Fix bug")
- Appropriate length
- Follows conventional commits format

**PR Description Checks:**
- Has meaningful description (not empty)
- Explains "why" not just "what"
- Includes testing notes
- Not copy-pasted template with blanks

**Commit Message Checks:**
- Individual commit messages are meaningful
- Not all identical
- Not auto-generated without editing
- Reasonable commit count

**File Change Checks:**
- Reasonable number of files changed
- No unrelated changes bundled
- No large binary files added
- No sensitive files included

**User Signal Checks:**
- Account age and activity
- Contribution history
- Profile completeness

**Contributor History:**
- Past PR quality
- Review participation
- Issue engagement

---

## Tool 3: Desloppify (peteromallet)

### Approach
Agent harness that combines:
1. **Mechanical detection** — Static analysis for known slop patterns
2. **Subjective LLM review** — AI evaluation of code quality, readability, maintainability
3. **Anti-gaming mechanisms** — Prevents the AI from marking its own slop as clean

### Workflow
```
1. Identify slop → Static analysis + LLM scan
2. Understand context → Why does this code exist?
3. Improve systematically → Refactor with understanding
4. Verify improvement → Independent review
```

Supports 29 languages.

---

## Integration Pattern for Objective Hertz

### Email Pipeline Quality Gate
```python
# In titan/pipeline/email_compose.py

async def compose_and_validate_email(lead, research_context):
    # Step 1: Compose with primary model
    email = await llm.compose_email(lead, research_context)

    # Step 2: Anti-slop check with FRESH model call (no conversation history)
    slop_score = await llm.evaluate_slop(
        content=email,
        model="haiku",  # Cheap, fast
        system_prompt=ANTI_SLOP_PROMPT,
        # Key: NO access to composition context
    )

    # Step 3: Gate
    if slop_score.naturalness < 0.7:
        email = await llm.recompose_email(
            lead, research_context,
            feedback=slop_score.findings
        )

    return email
```

### Anti-Slop System Prompt for Email Review
```
You are reviewing a cold email for AI-generated patterns. Score 0-1 on:

1. NATURALNESS: Does it read like a human wrote it? No "leverage", "streamline",
   "I hope this email finds you well", "I noticed that your business..."

2. SPECIFICITY: Does it reference actual details about the business, or could
   this email be sent to anyone? Generic = slop.

3. CONCISENESS: Is it under 150 words? Does every sentence earn its place?

4. AUTHENTICITY: Does it sound like a real person who looked at their business,
   or an AI that processed a data row?

Flag any of these patterns:
- "I came across your business" (AI tell)
- "In today's digital landscape" (slop opener)
- Three or more sentences starting with "I"
- Any sentence with "leverage", "optimize", "streamline"
- Bullet points in a cold email (nobody does this)
```

### Site Copy Quality Gate (ClawdBot)
Same pattern applied to `clawdbot/site_builder.py` output. Review generated site copy with a fresh LLM call before deploying to Netlify.

---

## Secret Detection Patterns (Most Common)

```python
PATTERNS = {
    "aws_key": r"AKIA[0-9A-Z]{16}",
    "stripe_live": r"sk_live_[a-zA-Z0-9]{24,}",
    "stripe_restricted": r"rk_live_[a-zA-Z0-9]{24,}",
    "anthropic": r"sk-ant-[a-zA-Z0-9-]+",
    "openai": r"sk-[a-zA-Z0-9]{48,}",
    "github_pat": r"ghp_[a-zA-Z0-9]{36}",
    "github_oauth": r"gho_[a-zA-Z0-9]{36}",
    "slack_token": r"xox[bpors]-[a-zA-Z0-9-]+",
    "generic_secret": r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"][^'\"]{8,}",
    "private_key": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "jwt": r"eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*",
}
```

---

## Sources

- [Anti-Slop Skill (DataWhisker)](https://github.com/DataWhisker/anti-slop-skill)
- [Anti-Slop GitHub Action (peakoss)](https://github.com/peakoss/anti-slop)
- [Desloppify (peteromallet)](https://github.com/peteromallet/desloppify)
- [Anti-AI Slop Writing](https://github.com/jalaalrd/anti-ai-slop-writing)
