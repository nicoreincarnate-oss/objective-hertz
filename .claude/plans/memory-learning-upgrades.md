# Memory & Learning System Upgrades — Implementation Plan

## Overview
18 improvements to Titan's memory, training, and learning systems, organized into 4 implementation phases. Each change touches specific files and has concrete acceptance criteria.

---

## Phase 1: Training & Memory Hygiene (Week 1)
*Fix the foundation before adding capabilities.*

### 1.1 — OPLoRA in titan/training.py
**What:** Replace standard LoRA with Orthogonal Projection LoRA (OPLoRA) to prevent catastrophic forgetting. Same Unsloth pipeline, just change the projection config.
**Files:** `titan/training.py` (lines 178-280)
**Changes:**
- In `_generate_training_script()`, change the LoRA config from:
  ```python
  lora_r=16, lora_alpha=32
  ```
  to OPLoRA config:
  ```python
  lora_r=16, lora_alpha=32, use_rslora=True, loftq_config=None
  ```
  Unsloth supports OPLoRA natively via `use_rslora=True` + orthogonal init
- Add `"orthogonal_init": True` to the training script's model.get_peft_model() call
- Add a `TRAINING_METHOD = "oplora"` constant at module level for logging/tracking
- Update the training metadata stored in titan_learnings to include method type
**Test:** Unit test that `_generate_training_script()` output contains `use_rslora=True`

### 1.2 — memory_gc() in perseus/sleep_cycle.py
**What:** Add garbage collection to the nightly sleep cycle. Prune low-confidence old learnings, merge duplicate Mem0 entries, deactivate contradicted rules.
**Files:** `titan/memory.py` (new function), `perseus/sleep_cycle.py` (wire into run_sleep_cycle)
**Changes:**
- New `memory_gc()` async function in `titan/memory.py`:
  ```python
  async def memory_gc():
      # 1. Prune titan_learnings with confidence < 0.4 older than 90 days
      pruned = await execute(
          """DELETE FROM titan_learnings
             WHERE confidence < 0.4
             AND created_at < NOW() - INTERVAL '90 days'
             RETURNING id"""
      )

      # 2. Deduplicate: find titan_learnings with near-identical insight text
      #    (same category, Levenshtein similarity > 0.85, keep highest confidence)
      dupes = await fetch_all(
          """SELECT a.id as keep_id, b.id as remove_id
             FROM titan_learnings a
             JOIN titan_learnings b ON a.category = b.category
               AND a.id < b.id
               AND a.confidence >= b.confidence
               AND similarity(a.insight, b.insight) > 0.85
             WHERE a.created_at > NOW() - INTERVAL '90 days'"""
      )
      # Delete the lower-confidence duplicates

      # 3. Deactivate titan_rules that contradict each other
      #    (same metric_name, opposite direction implications)
      #    Flag for Alpha/Beta debate rather than auto-delete

      # 4. Mem0 cleanup via API: list all memories, identify near-duplicates
      #    Merge by keeping the most recent + highest-metadata entry
  ```
- In `perseus/sleep_cycle.py` `run_sleep_cycle()`, add `await memory_gc()` as the first step (clean before optimizing)
**Dependency:** Postgres `pg_trgm` extension for `similarity()` function. Check in init-db.sql.
**Test:** Insert 5 old low-confidence learnings, run memory_gc(), verify they're gone.

### 1.3 — Confidence decay for titan_rules
**What:** Rules that haven't been validated by a metric improvement in 30 days auto-decrement confidence. Eventually they fall below threshold and stop being injected.
**Files:** `titan/memory.py` (modify `evaluate_rules()`)
**Changes:**
- In `evaluate_rules()`, after the current regression check, add:
  ```python
  # Confidence decay: rules not validated in 30 days lose 0.1 confidence
  stale_rules = await fetch_all(
      """SELECT id, confidence FROM titan_rules
         WHERE active = TRUE
         AND (evaluated_at IS NULL OR evaluated_at < NOW() - INTERVAL '30 days')
         AND created_at < NOW() - INTERVAL '30 days'"""
  )
  for rule in stale_rules:
      new_conf = max(0.1, rule["confidence"] - 0.1)
      await execute(
          "UPDATE titan_rules SET confidence = %s, evaluated_at = NOW() WHERE id = %s",
          (new_conf, rule["id"])
      )
      if new_conf <= 0.3:
          await execute("UPDATE titan_rules SET active = FALSE WHERE id = %s", (rule["id"],))
  ```
- Rules below 0.3 confidence get auto-deactivated
- This prevents "zombie rules" that sit forever at initial confidence
**Test:** Create a rule with confidence 0.5, mock time forward 60 days, run evaluate_rules() twice, verify confidence drops to 0.3 then deactivates.

### 1.4 — Contradiction detection in daily_reflection()
**What:** Before inserting a new insight, check if it directly contradicts an existing titan_learning. Merge or flag for Alpha/Beta debate.
**Files:** `titan/memory.py` (modify `daily_reflection()`)
**Changes:**
- After LLM generates insights (line 179) but before storing (line 186), add:
  ```python
  for insight in insights:
      # Check for contradiction with existing high-confidence learnings
      existing = await fetch_all(
          """SELECT id, insight, confidence FROM titan_learnings
             WHERE category = %s AND confidence > 0.6
             AND created_at > NOW() - INTERVAL '30 days'
             ORDER BY confidence DESC LIMIT 5""",
          (insight.get("category"),)
      )
      if existing:
          contradiction_check = await llm.generate(
              f"New insight: {insight['insight']}\n\n"
              f"Existing insights:\n" +
              "\n".join(f"- {e['insight']} (confidence: {e['confidence']})" for e in existing) +
              f"\n\nDoes the new insight DIRECTLY CONTRADICT any existing insight? "
              f"Return JSON: {{\"contradicts\": true/false, \"contradicted_id\": <id or null>, \"resolution\": \"keep_new|keep_old|merge|debate\"}}",
              model="fast", temperature=0.1
          )
          # Parse and handle: merge, replace, or flag for sleep cycle debate
  ```
- If `resolution == "debate"`, store as a `pending_contradiction` event for the sleep cycle's Alpha/Beta to resolve
- If `resolution == "merge"`, update the existing learning's text with merged content
- If `resolution == "keep_new"`, deactivate the old one and insert new
**Test:** Insert learning "Short subjects work best", then try to insert "Longer descriptive subjects get more opens". Verify contradiction detected.

---

## Phase 2: Delayed Outcomes & Attribution (Week 2)
*Fix the training data quality problem.*

### 2.1 — Outcome delay tracking
**What:** Deals close weeks after email. Add a `pending_outcome` state and background job that re-checks lead status at 7/14/30 days for accurate training labels.
**Files:** `titan/training.py` (new function), `perseus/scheduler.py` (new scheduled task), `titan/daemon.py` (new handler)
**Changes:**
- New DB table (migration):
  ```sql
  CREATE TABLE pending_outcomes (
      id SERIAL PRIMARY KEY,
      training_data_id INT REFERENCES training_data(id),
      client_id INT,
      email_seq_id INT,
      check_at TIMESTAMPTZ NOT NULL,
      check_type TEXT NOT NULL, -- '7day', '14day', '30day'
      checked BOOLEAN DEFAULT FALSE,
      created_at TIMESTAMPTZ DEFAULT NOW()
  );
  ```
- Modify `collect_email_outcome()`: when outcome is empty or 'sent', create 3 pending_outcome rows (7/14/30 days out)
- New `check_pending_outcomes()` function:
  ```python
  async def check_pending_outcomes():
      pending = await fetch_all(
          """SELECT po.*, td.example_type, td.input_text, td.output_text
             FROM pending_outcomes po
             JOIN training_data td ON td.id = po.training_data_id
             WHERE po.check_at <= NOW() AND po.checked = FALSE"""
      )
      for p in pending:
          current_status = await fetch_val(
              "SELECT status FROM clients WHERE id = %s", (p["client_id"],)
          )
          # Map current status to training outcome
          # Update training_data row with accurate delayed outcome
          # Mark pending_outcome as checked
  ```
- Add to `perseus/scheduler.py`: `Schedule("check_pending_outcomes", 3600, "Re-check lead outcomes for training accuracy", skippable=True, pipeline_stage="learning")`
- Add handler to `titan/daemon.py` TASK_HANDLERS
**Test:** Create email with no outcome, advance time 7 days, simulate lead becoming "interested", verify training_data updated.

### 2.2 — Causal credit assignment (positive AND negative)
**What:** When ANY reply comes in, ask Claude which sentence caused it. For positive replies: what hooked them. For negative/no-response: what killed them. The contrast is where the real signal lives.
**Files:** `titan/pipeline/follow_up.py` (modify `_process_reply()`), `titan/training.py` (new function)
**Changes:**
- New `attribute_reply_cause()` in `titan/training.py`:
  ```python
  async def attribute_reply_cause(original_email: str, reply_body: str, outcome: str):
      if outcome == "positive":
          prompt = (
              f"Original cold email:\n{original_email}\n\n"
              f"Prospect's positive reply:\n{reply_body}\n\n"
              f"Which specific sentence or element in the original email most likely "
              f"triggered this positive response? Consider: the hook, the value prop, "
              f"the social proof, the CTA, personalization, timing reference.\n\n"
              f"Return JSON: {{\"trigger_sentence\": \"...\", \"trigger_type\": "
              f"\"hook|value_prop|social_proof|cta|personalization|timing\", "
              f"\"effect\": \"positive\", \"confidence\": 0.0-1.0}}"
          )
      elif outcome == "negative":
          prompt = (
              f"Original cold email:\n{original_email}\n\n"
              f"Prospect's negative reply:\n{reply_body}\n\n"
              f"Which specific sentence or element most likely caused this negative "
              f"reaction? Look for: generic claims, pushy CTA, wrong tone, irrelevant "
              f"value prop, spam-sounding phrases, factual errors about their business.\n\n"
              f"Return JSON: {{\"trigger_sentence\": \"...\", \"trigger_type\": "
              f"\"generic_claim|pushy_cta|wrong_tone|irrelevant_offer|spam_phrase|factual_error\", "
              f"\"effect\": \"negative\", \"confidence\": 0.0-1.0}}"
          )
      else:
          return

      result = await llm.generate(prompt, model="fast", temperature=0.2)
      # Parse, store in titan_learnings category='sentence_attribution'
      # Also store in training_data as example_type='attribution'
  ```
- Also add `attribute_silence()` for emails with 0 response after 14 days:
  ```python
  async def attribute_silence(original_email: str):
      """Analyze emails that got zero engagement — what pattern killed them?"""
      result = await llm.generate(
          f"This cold email got zero opens or replies after 14 days:\n\n"
          f"{original_email}\n\n"
          f"Identify the most likely reason it failed. Focus on: subject line, "
          f"first sentence, length, specificity, spam signals.\n\n"
          f"Return JSON: {{\"likely_cause\": \"...\", \"trigger_type\": "
          f"\"weak_subject|bad_opening|too_long|too_generic|spam_trigger|wrong_audience\", "
          f"\"effect\": \"silence\", \"confidence\": 0.0-1.0}}",
          model="fast", temperature=0.2
      )
      # Store as negative attribution signal
  ```
- Call `attribute_silence()` from `check_pending_outcomes()` (2.1) when 14-day check finds zero engagement
- The positive vs negative vs silence contrast is where the real signal lives
**Test:** Mock a positive reply, negative reply, and a silent email. Verify 3 different attribution types stored.

### 2.3 — memory_importance_score on every Mem0 write
**What:** Weight memories by recency × outcome magnitude × sample size so retrieval surfaces statistically significant memories first.
**Files:** `titan/memory.py` (modify `store_memory()`, `get_relevant_learnings()`)
**Changes:**
- Modify `store_memory()` to accept and store importance metadata:
  ```python
  async def store_memory(content, category, client_id=None, metadata=None,
                         outcome_magnitude=0.5, sample_size=1):
      importance = _compute_importance(outcome_magnitude, sample_size)
      mem_metadata = {
          "category": category,
          "importance": importance,
          "sample_size": sample_size,
          ...
      }
  ```
- New `_compute_importance()`:
  ```python
  def _compute_importance(outcome_magnitude: float, sample_size: int) -> float:
      # Bayesian-flavored: weight by evidence strength
      evidence_weight = min(1.0, sample_size / 100)  # saturates at n=100
      return round(outcome_magnitude * evidence_weight, 3)
  ```
- Modify `get_relevant_learnings()` to sort by importance when Mem0 returns results
- Mem0 metadata filtering: request `filter={"importance": {"$gte": 0.3}}` to skip low-signal noise
**Test:** Store 3 memories with different importance scores, search, verify highest-importance returned first.

---

## Phase 3: Temporal Memory & Cross-Agent Learning (Week 3)

### 3.1 — Zep/Graphiti as 3rd memory backend
**What:** Add temporal fact store alongside Qdrant. Handles fact expiry so stale memories stop poisoning prompts.
**Files:** `titan/memory.py` (new functions), `shared/config.py` (new config section)
**Changes:**
- Add `ZepConfig` to `shared/config.py`:
  ```python
  @dataclass(frozen=True)
  class ZepConfig:
      zep_url: str = field(default_factory=lambda: os.getenv("ZEP_URL", "http://localhost:8000"))
      enabled: bool = field(default_factory=lambda: os.getenv("ZEP_ENABLED", "0") == "1")
  ```
- New functions in `titan/memory.py`:
  ```python
  async def store_temporal_fact(content, category, valid_until=None, source_lead_id=None):
      """Store a fact with optional expiry. Facts auto-expire and stop appearing in searches."""
      # POST to Zep/Graphiti API

  async def search_temporal_facts(query, limit=5):
      """Search temporal facts, auto-excluding expired ones."""
      # GET from Zep/Graphiti with temporal filtering

  async def expire_stale_facts():
      """Called by memory_gc(). Mark facts past their valid_until as expired."""
  ```
- Modify `get_relevant_learnings()` to include temporal facts as a 3rd source WITH merge priority:
  ```python
  async def get_relevant_learnings(context: str, limit: int = 10) -> str:
      # 1. Structured learnings from Postgres (high confidence first)
      db_learnings = await fetch_all(...)

      # 2. Vector search from Mem0/Qdrant (semantic similarity)
      vector_memories = await search_memory(context, limit=limit)

      # 3. Temporal facts from Zep (only non-expired)
      temporal_facts = await search_temporal_facts(context, limit=5) if zep_enabled else []

      # MERGE PRIORITY PROTOCOL:
      # - Zep WINS on anything with a timestamp (it tracks when facts expire)
      # - Qdrant WINS on pure semantic match with no temporal component
      # - If Zep says "dentists respond to X" expired 3 weeks ago but Qdrant
      #   still has it, Zep's expiry takes precedence — the fact is stale
      #
      # Implementation: tag each result with source + timestamp
      # If a Zep fact contradicts a Qdrant memory on the same topic:
      #   - If Zep fact is non-expired → use Zep version
      #   - If Zep fact IS expired → exclude the Qdrant memory too (it's stale)
      #   - If no Zep fact exists on topic → use Qdrant as-is

      parts = []
      if temporal_facts:
          parts.append("CURRENT FACTS (verified fresh, trust these over older memories):")
          for f in temporal_facts:
              parts.append(f"  - {f['content']} [valid until {f.get('valid_until', 'indefinite')}]")
      if db_learnings:
          parts.append("STRUCTURED INSIGHTS:")
          for l in db_learnings:
              parts.append(f"  [{l['category']}] {l['insight']}")
      if vector_memories:
          # Filter out any Qdrant memories that Zep has expired
          filtered = _filter_stale_qdrant(vector_memories, temporal_facts)
          if filtered:
              parts.append("RELEVANT MEMORIES:")
              for m in filtered:
                  parts.append(f"  - {m}")

      return "\n".join(parts) if parts else "No prior learnings yet."
  ```
- Graceful degradation: if Zep not configured/enabled, skip silently (same pattern as Mem0)
- **Conflict resolution:** Zep wins on timestamped facts, Qdrant wins on pure semantic. No ambiguity for Claude.
**Test:** Store fact with 24h expiry, verify it appears in search, advance time 25h, verify it doesn't. Also: store contradicting fact in Qdrant and expired fact in Zep, verify Qdrant version is filtered out.

### 3.2 — Weekly graphrag_consolidation() job
**What:** Cluster Mem0 entries by industry+region+outcome, summarize into single high-signal nodes.
**Files:** `titan/memory.py` (new function), `perseus/scheduler.py` (new task)
**Changes:**
- New `graphrag_consolidation()`:
  ```python
  async def graphrag_consolidation():
      # 1. Fetch all Mem0 memories from last 7 days
      # 2. Group by metadata.category
      # 3. For each group with 5+ entries, ask LLM to summarize:
      #    "Consolidate these {n} memories into 1-3 high-signal insights"
      # 4. Store consolidated memories with high importance score
      # 5. Delete (or mark) the individual memories that were consolidated
      # This dramatically reduces context noise in prompt injection
  ```
- Add to scheduler: `Schedule("graphrag_consolidation", 604800, "Weekly memory consolidation", skippable=False)`
**Test:** Insert 10 similar Mem0 entries about "dental clinic email performance", run consolidation, verify reduced to 2-3 entries.

### 3.3 — Cross-agent memory: ClawdBot skill outcomes → titan_rules
**What:** ClawdBot's skill success/failure rates don't currently feed Titan's rules. Add a `skill_performance` learning category.
**Files:** `clawdbot/daemon.py` (modify task completion path), `titan/memory.py` (new function)
**Changes:**
- In `clawdbot/daemon.py`, after any skill execution completes (success or failure), call:
  ```python
  from shared.comms import store_learning
  await store_learning(
      category="skill_performance",
      insight=f"Skill '{skill_name}' {'succeeded' if success else 'failed'} for task '{task_type}': {result_summary}",
      confidence=0.7 if success else 0.4
  )
  ```
- In `titan/memory.py`, modify `get_relevant_learnings()` to include skill_performance when context mentions building/scraping/tools
- New `get_skill_success_rates()` function that queries titan_learnings for skill_performance category and computes success rates per skill
- Titan's expansion system reads these rates when deciding which skills to prefer
**Test:** Log 5 successes and 2 failures for a skill, call get_skill_success_rates(), verify correct rate computed.

---

## Phase 4: Advanced Intelligence (Week 4)
*The features that make the system genuinely smarter.*

### 4.1 — Beta reads git diff of last 7 cycles
**What:** Beta currently debates proposals in a vacuum. Give it history of what was tried and reverted.
**Files:** `perseus/sleep_cycle.py` (modify `_run_beta()`, `_gather_system_snapshot()`)
**Changes:**
- In `_gather_system_snapshot()`, add:
  ```python
  # Recent sleep cycle history (what was tried, what stuck)
  recent_cycles = await fetch_all(
      """SELECT cycle_date,
              applied_changes::text,
              rolled_back
         FROM sleep_cycle_log
         ORDER BY cycle_date DESC LIMIT 7"""
  )
  snapshot["recent_cycle_history"] = [dict(c) for c in recent_cycles] if recent_cycles else []

  # Git diff of backprop-touched files
  import subprocess
  try:
      diff = subprocess.run(
          ["git", "log", "--oneline", "-7", "--", "soul/", "titan/pipeline/"],
          capture_output=True, text=True, timeout=5
      ).stdout[:2000]
      snapshot["recent_git_changes"] = diff
  except Exception:
      snapshot["recent_git_changes"] = ""
  ```
- In `_run_beta()` prompt, add:
  ```
  RECENT CYCLE HISTORY (what was already tried):
  {snapshot.get("recent_cycle_history", "None")}

  RECENT GIT CHANGES:
  {snapshot.get("recent_git_changes", "None")}

  Use this history to avoid re-proposing changes that were already tried and reverted.
  If Alpha proposes something similar to a reverted change, flag it.
  ```
**Test:** Create 3 sleep_cycle_log entries (1 rolled back), run Beta, verify prompt includes history.

### 4.2 — 4-agent red-team on high-stakes emails (proposals)
**What:** Run a 4-agent pipeline on every $299+ proposal email: Writer → Attacker → Optimizer → Tone Checker.
**Files:** `titan/pipeline/close_deal.py` (modify `_generate_proposal()`)
**Changes:**
- New `_red_team_proposal()` in `titan/pipeline/close_deal.py`:
  ```python
  async def _red_team_proposal(proposal_text, lead, price):
      # Agent 1 (Writer): already generated the proposal

      # Agent 2 (Attacker): plays the prospect
      attack = await llm.generate(
          f"You are {lead.get('business_name')}'s owner. You're busy, skeptical, "
          f"and have been burned by web agencies before. Read this proposal and "
          f"explain exactly why you WON'T reply:\n\n{proposal_text}\n\n"
          f"Be specific: what feels generic? What's missing? What would make "
          f"you hit delete?",
          model="smart", temperature=0.6
      )

      # Agent 3 (Optimizer): synthesizes
      optimized = await llm.generate(
          f"Original proposal:\n{proposal_text}\n\n"
          f"Prospect's likely objections:\n{attack}\n\n"
          f"Rewrite the proposal to preemptively address every objection. "
          f"Keep it under 200 words. Make it impossible to ignore.",
          model="smart", temperature=0.4
      )

      # Agent 4 (Tone Checker): anti-AI-voice gate
      tone_check = await llm.generate(
          f"Read this cold email proposal and flag problems:\n\n{optimized}\n\n"
          f"Check for:\n"
          f"1. AI-sounding phrases ('leverage', 'streamline', 'I'd love to', 'excited to')\n"
          f"2. Overly formal tone (no human talks like this in an email)\n"
          f"3. Spam trigger words (guarantee, limited time, act now, exclusive)\n"
          f"4. Generic filler that could apply to any business\n"
          f"5. Sentences longer than 20 words\n\n"
          f"Return JSON: {{\"passes\": true/false, \"issues\": [\"...\"], "
          f"\"rewrite\": \"...\" (only if passes=false, rewrite fixing all issues)}}",
          model="fast", temperature=0.2
      )
      # Parse tone_check. If passes=false, use the rewrite.
      # If rewrite also fails a second tone check, use the optimizer output
      # (cap at 2 rewrites to prevent infinite loops)
      return final_text
  ```
- Call before `_send_proposal()`, only for leads with score >= 70 or deal value >= $299
- Budget: ~$0.03-0.06 per proposal (Sonnet × 3 + Haiku × 1). The $0.01 tone check prevents a $299 deal dying to robotic copy.
**Test:** Mock a proposal with AI-sounding phrases, verify tone checker flags them, verify output sounds human.

### 4.3 — Continuous lead re-enrichment (48h background job)
**What:** Re-research active leads every 48 hours. Their Google reviews, Facebook posts, and recent complaints tell you what's bothering them RIGHT NOW.
**Files:** `titan/pipeline/lead_research.py` (new function), `perseus/scheduler.py` (new task)
**Changes:**
- New `re_enrich_active_leads()`:
  ```python
  async def re_enrich_active_leads():
      # Find leads that are interested/in-progress and haven't been enriched in 48h
      stale_leads = await fetch_all(
          """SELECT id, business_name, website, email
             FROM clients
             WHERE status IN ('interested', 'demo_built', 'proposal_sent', 'negotiating')
             AND (enriched_at IS NULL OR enriched_at < NOW() - INTERVAL '48 hours')
             LIMIT 10"""
      )
      for lead in stale_leads:
          # Scrape fresh: Google reviews, social media, recent news
          # Compare with existing research_summary
          # If significant new info, update research_facts and notify pipeline
          # Store delta as temporal fact in Zep (valid 7 days)
  ```
- Add `enriched_at` column to clients table (migration)
- Add to scheduler: `Schedule("re_enrich_leads", 86400, "Re-enrich active leads", skippable=True)`
**Test:** Create a lead last enriched 3 days ago with status "interested", run re_enrich, verify enriched_at updated.

### 4.4 — Prospect emotional state modeling
**What:** Track a running psychological profile per lead: contact count, reply tones, open-to-reply timing, click behavior. Build a `prospect_state` that Titan reads before every touchpoint.
**Files:** `titan/pipeline/follow_up.py` (modify `_process_reply`), `titan/memory.py` (new functions)
**Changes:**
- New `prospect_state` JSONB column on clients table (migration)
- New `update_prospect_state()` in `titan/memory.py`:
  ```python
  async def update_prospect_state(client_id, event_type, data):
      current = await fetch_val(
          "SELECT prospect_state FROM clients WHERE id = %s", (client_id,)
      ) or {}

      current["contact_count"] = current.get("contact_count", 0) + (1 if event_type == "email_sent" else 0)
      current["last_contact"] = data.get("timestamp")

      if event_type == "reply_received":
          # Classify emotional tone of reply
          tone = await llm.generate(
              f"Classify the emotional tone of this reply in 1-2 words: {data['body'][:500]}",
              model="fast"
          )
          current.setdefault("tone_history", []).append(tone.strip()[:30])
          current["reply_speed_hours"] = data.get("hours_since_last_contact")

      if event_type == "email_opened":
          current["opens_without_reply"] = current.get("opens_without_reply", 0) + 1

      await execute(
          "UPDATE clients SET prospect_state = %s WHERE id = %s",
          (json.dumps(current), client_id)
      )
  ```
- Inject prospect_state into compose and follow_up prompts so Titan knows whether to be persistent, gentle, or pivot approach
- **Low-confidence escalation:** When tone classification confidence < 0.5 OR when state is ambiguous (e.g., 3+ opens but no reply, or mixed tone history), set `low_confidence_state = True` and alert via Hermes:
  ```python
  if tone_confidence < 0.5 or _is_ambiguous_state(current):
      current["low_confidence_state"] = True
      await emit_event("urgent_alert", {
          "sender": "titan.prospect_state",
          "message": f"Ambiguous prospect state for {lead_name} "
                     f"(status: {current.get('last_tone', '?')}, "
                     f"opens: {current.get('opens_without_reply', 0)}, "
                     f"contacts: {current.get('contact_count', 0)}). "
                     f"Review before next touchpoint.",
      })

  def _is_ambiguous_state(state: dict) -> bool:
      """Flag states where automated follow-up could backfire."""
      # 5+ opens with no reply = interested but hesitant, don't push too hard
      if state.get("opens_without_reply", 0) >= 5:
          return True
      # Mixed tone history (positive then negative) = confused, needs human eye
      tones = state.get("tone_history", [])
      if len(tones) >= 2 and any("negative" in t.lower() for t in tones) and any("positive" in t.lower() for t in tones):
          return True
      return False
  ```
- This prevents the system from confidently backing off a hot lead because it misread a tone
**Test:** Simulate 3 events (send, open, reply), verify prospect_state accumulates. Then simulate ambiguous state (5 opens, no reply), verify Hermes alert fires.

### 4.5 — Rolling 30-day training window
**What:** Instead of training one model on all data ever, maintain a rolling window. Old examples age out. The model stays tuned to current market behavior.
**Files:** `titan/training.py` (modify `export_training_data()`)
**Changes:**
- Modify `export_training_data()`:
  ```python
  # Before: exports ALL labeled examples
  # After: exports only last 30 days, with recency weighting
  examples = await fetch_all(
      """SELECT example_type, input_text, output_text, outcome,
              EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400 as age_days
         FROM training_data
         WHERE outcome IN ('positive', 'negative')
         AND created_at > NOW() - INTERVAL '30 days'
         ORDER BY created_at DESC""",
  )

  # Weight recent examples higher: duplicate examples from last 7 days
  recent = [e for e in examples if e["age_days"] <= 7]
  # Include recent examples twice in the JSONL (recency boost)
  ```
- This means the fine-tuned model reflects current market behavior, not 4-month-old patterns
- Old examples still exist in DB for analysis, just not used for training
**Test:** Insert examples at 5 days, 15 days, and 45 days ago. Export. Verify 45-day example excluded, 5-day example duplicated.

### 4.6 — A/B testing as first-class primitive
**What:** Always run 2 variants on every new lead batch. Statistically clean outcome data.
**Files:** `titan/pipeline/email_compose.py` (modify `compose_emails()`), `titan/memory.py` (new analysis function)
**Changes:**
- Modify `compose_emails()` to always generate 2 variants for each batch:
  ```python
  # Split batch into A/B cohorts
  cohort_a = leads[:len(leads)//2]
  cohort_b = leads[len(leads)//2:]

  # Compose A with current best approach
  # Compose B with one deliberate variation (subject style, CTA, tone)
  variation = await _pick_next_ab_variation()  # rotate through: subject_length, cta_style, tone, personalization_depth

  # Tag each email with ab_cohort and ab_variation in metadata
  ```
- New `analyze_ab_results()` function called by weekly_strategy_review:
  ```python
  AB_MIN_SAMPLE_PER_VARIANT = 30  # hard floor — no evaluation below this

  async def analyze_ab_results():
      # 1. Query outreach_metrics grouped by ab_cohort + ab_variation
      active_tests = await fetch_all(
          """SELECT ab_variation,
                    ab_cohort,
                    COUNT(*) as n,
                    SUM(CASE WHEN outcome = 'positive' THEN 1 ELSE 0 END) as successes
             FROM training_data
             WHERE ab_cohort IS NOT NULL
             AND created_at > NOW() - INTERVAL '14 days'
             GROUP BY ab_variation, ab_cohort"""
      )

      for test in _pair_ab_tests(active_tests):
          # 2. Minimum sample gate — skip if either variant has n < 30
          if test["a"]["n"] < AB_MIN_SAMPLE_PER_VARIANT or test["b"]["n"] < AB_MIN_SAMPLE_PER_VARIANT:
              logger.info(f"A/B test '{test['variation']}' needs more data: "
                          f"A={test['a']['n']}, B={test['b']['n']} (need {AB_MIN_SAMPLE_PER_VARIANT} each)")
              continue

          # 3. Chi-squared test for proportions (scipy.stats.chi2_contingency)
          #    p < 0.05 = significant, otherwise keep running
          # 4. If significant winner, promote variation as new default
          #    Store as titan_rule with high confidence (data-backed, n >= 60)
          # 5. If neither wins after 14 days + sufficient n, discard test
  ```
- **Minimum sample gate (n≥30 per variant)** prevents promoting garbage rules from 5-email batches
- This generates 10x cleaner training data than organic variance
**Test:** Compose 10 emails, verify 5 tagged cohort_a and 5 tagged cohort_b. Then verify analyze_ab_results() skips evaluation when n < 30.

### 4.7 — Competitor intelligence loop
**What:** Point the Scout at actual competitors (agencies, freelancers in target cities). Watch pricing pages, LinkedIn, testimonials. Feed into competitive_intel learning category.
**Files:** `orchestrator.py` (modify `_scout_loop`), `titan/memory.py` (add competitive analysis)
**Changes:**
- Add competitor monitoring to scout sources:
  ```python
  COMPETITOR_SOURCES = [
      {"type": "google", "query": "web design agency {city} pricing"},
      {"type": "google", "query": "small business website cost 2026"},
      {"type": "linkedin", "query": "freelance web designer {region}"},
  ]
  ```
- New `store_competitive_intel()` function in titan/memory.py
- **Reactive action layer — not just observation:**
  ```python
  COMPETITIVE_THRESHOLDS = {
      "price_undercut": 249,      # if competitor drops below this, trigger alert
      "new_competitor": True,      # any new agency in our target cities
      "feature_gap": True,         # competitor offers something we don't
  }

  async def process_competitive_finding(finding: dict):
      category = finding.get("category")

      # 1. Always store the intel
      await store_learning(
          category="competitive_intel",
          insight=json.dumps(finding),
          confidence=finding.get("confidence", 0.5)
      )

      # 2. Reactive triggers — don't wait for weekly review
      if category == "pricing" and finding.get("price", 999) < COMPETITIVE_THRESHOLDS["price_undercut"]:
          # Competitor undercut detected — immediate Hermes alert
          await emit_event("urgent_alert", {
              "sender": "scout.competitive_intel",
              "message": f"⚠️ Competitor '{finding.get('name')}' dropped price to "
                         f"${finding.get('price')}. Our current: $299. "
                         f"Flag for Alpha/Beta pricing debate tonight.",
          })
          # Queue a pricing debate for tonight's sleep cycle
          await emit_event("pending_contradiction", {
              "type": "pricing_pressure",
              "source": "competitive_intel",
              "data": finding,
              "action": "alpha_beta_debate",
          })

      if category == "feature" and finding.get("feature_type") == "ai_site_builder":
          # Competitor launched similar capability — alert + evaluate
          await emit_event("agent_recommendation", {
              "from_agent": "scout",
              "to_agent": "clawdbot",
              "topic": "competitive_capability",
              "message": f"Competitor '{finding.get('name')}' offers: {finding.get('description')}. "
                         f"Evaluate if we should integrate or counter.",
          })
  ```
- Weekly strategy review reads competitive_intel to adjust pricing and positioning
- **Immediate alerts** for price undercuts below $249 and new AI builder competitors
- Sleep cycle Alpha/Beta can debate pricing changes that same night instead of waiting a week
**Test:** Mock a scout finding with competitor price $199, verify Hermes alert fires immediately AND pending_contradiction event queued for sleep cycle.

---

## Migration Requirements

New DB migrations needed (single file: `scripts/migrations/007-memory-upgrades.sql`):
```sql
-- Pending outcomes for delayed training labels
CREATE TABLE IF NOT EXISTS pending_outcomes (
    id SERIAL PRIMARY KEY,
    training_data_id INT,
    client_id INT,
    email_seq_id INT,
    check_at TIMESTAMPTZ NOT NULL,
    check_type TEXT NOT NULL,
    checked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_pending_outcomes_check ON pending_outcomes(check_at) WHERE NOT checked;

-- Prospect state column
ALTER TABLE clients ADD COLUMN IF NOT EXISTS prospect_state JSONB DEFAULT '{}';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;

-- AB testing metadata on email sequences
ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS ab_cohort TEXT;
ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS ab_variation TEXT;

-- Confidence decay tracking on rules
ALTER TABLE titan_rules ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMPTZ;

-- Enable trigram for memory dedup
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Prompt versioning (5.1) — trace which prompt version produced which outcome
ALTER TABLE training_data ADD COLUMN IF NOT EXISTS prompt_version_hash TEXT;
ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS prompt_version_hash TEXT;
CREATE INDEX idx_training_prompt_version ON training_data(prompt_version_hash) WHERE prompt_version_hash IS NOT NULL;
```

## New Scheduled Tasks (add to perseus/scheduler.py)
```python
Schedule("check_pending_outcomes", 3600, "Re-check lead outcomes for training accuracy", skippable=True, pipeline_stage="learning"),
Schedule("re_enrich_leads", 86400, "Re-enrich active leads with fresh data", skippable=True, pipeline_stage="top_of_funnel"),
Schedule("graphrag_consolidation", 604800, "Weekly memory consolidation", skippable=False),
```

---

## Phase 5: Prompt Versioning (Add to Week 1 — costs nothing, enables everything else)

### 5.1 — prompt_version_hash on all outputs
**What:** Every time the sleep cycle edits a soul doc, the rules list changes, or A/B testing modifies a prompt, the actual prompts Titan uses shift. But there's no way to know which prompt version produced which email outcome. After Phase 4 you'll have A/B testing, red-teaming, rule injection, and re-enrichment all modifying prompts simultaneously — and zero attribution of which change moved the needle.
**Files:** `titan/pipeline/email_compose.py`, `titan/training.py`, migration
**Changes:**
- Add `prompt_version_hash` column to `training_data` and `email_sequences`:
  ```sql
  ALTER TABLE training_data ADD COLUMN IF NOT EXISTS prompt_version_hash TEXT;
  ALTER TABLE email_sequences ADD COLUMN IF NOT EXISTS prompt_version_hash TEXT;
  ```
- New helper in `titan/memory.py`:
  ```python
  import hashlib

  def compute_prompt_version(soul_copy: str, rules: list[dict], ab_variation: str = "") -> str:
      """Hash the current prompt ingredients so we can trace which version produced which outcome."""
      content = soul_copy + "|" + json.dumps(rules, sort_keys=True) + "|" + ab_variation
      return hashlib.sha256(content.encode()).hexdigest()[:16]
  ```
- In `email_compose.py`, compute the hash before composing and store it on every email and training example
- In `analyze_ab_results()`, group by prompt_version_hash to see if a soul doc edit or rule change (not the A/B variation) was the real cause of a metric shift
- **Costs nothing.** One hash per email. Makes your entire causal chain auditable.
**Test:** Compose 2 emails with different rules lists, verify different prompt_version_hash. Change soul doc, verify hash changes.

---

## Implementation Order (Revised)

**Sequencing change:** GraphRAG consolidation (3.2) moves to Week 2 — you need clean consolidated memories BEFORE injecting prospect emotional state (4.4) and re-enrichment (4.3) into prompts. Otherwise you inject noise from 400 raw memories instead of 20 high-signal graph nodes.

1. **Week 1 (foundation + versioning):** 5.1 prompt_version_hash, 1.1 OPLoRA, 1.2 memory_gc, 1.3 confidence decay, 1.4 contradiction detection
2. **Week 2 (data quality + consolidation):** 3.2 graphrag consolidation (moved up), 2.1 outcome delay, 2.2 causal attribution (positive+negative+silence), 2.3 importance scores
3. **Week 3 (cross-agent + temporal):** 3.1 Zep/Graphiti (with conflict resolution protocol), 3.3 ClawdBot skill outcomes
4. **Week 4 (intelligence):** 4.1 Beta history, 4.2 4-agent red-team proposals (with tone checker), 4.3 re-enrichment, 4.4 prospect state (with Hermes escalation), 4.5 rolling window, 4.6 A/B testing (with n≥30 gate), 4.7 competitor intel (with reactive action layer)

## Budget Impact
- Phase 1-2: ~$0 additional (same LLM calls, just smarter routing)
- Phase 3: Zep hosting ~$0 (self-hosted Docker) or ~$20/mo (cloud)
- Phase 4: ~$2-5/day additional LLM spend (red-team proposals, re-enrichment, A/B analysis, tone checks)
- Total: +$60-170/month at growth mode
