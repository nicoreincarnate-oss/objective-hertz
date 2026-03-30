# Recursive Language Models (RLMs) — Reference

**Paper:** "Recursive Language Models" (arXiv:2512.24601, December 2025)
**Authors:** Alex L. Zhang, Tim Kraska, Omar Khattab — MIT CSAIL
**Implementation:** https://github.com/ysz/recursive-llm
**Deep Dive:** https://www.primeintellect.ai/blog/rlm

---

## Why This Matters for Objective Hertz

Titan's 10-stage pipeline loses context between stages because data passes through Postgres task_queue as flat records. By the time `email_compose.py` runs, the rich context from `lead_discovery.py` and `lead_research.py` has been compressed to database columns. RLMs solve this by treating long context as queryable environment variables instead of token-embedded prompts.

**Direct application:** Store full research output in Mem0/Qdrant. When `email_compose` runs, it recursively queries into the full research context to pull specific details (review quotes, pricing gaps, competitor names) rather than working from flat lead records.

---

## Core Architecture

### The REPL Paradigm
RLMs initialize a Read-Eval-Print Loop (REPL) environment where:
1. The full input prompt becomes a manipulable **string variable** in a Python environment
2. The model writes **code** that peeks into and decomposes the prompt
3. The model can **recursively call itself** (`llm_query()`) on sub-tasks
4. Results are assembled programmatically

### Three Core REPL Capabilities
1. **Context Variable** — Long text stored as a Python string, not fed as tokens
2. **`llm_query()` Function** — Self-invocation for recursive decomposition
3. **Python Execution** — Full code execution for filtering, regex, chunking

### Four-Step Execution Model
1. Context stored as Python variable in REPL
2. Root model receives only query + instructions (NOT the full context)
3. Model explores context via executable Python (slicing, regex, recursive calls)
4. Results returned via `FINAL(answer)` statement

---

## Key Innovation: Context as Environment, Not Tokens

Traditional approach:
```
[System Prompt] + [Full Context: 100k tokens] + [Query]
→ Model processes everything in one pass
→ Quality degrades as context grows ("context rot")
```

RLM approach:
```
[System Prompt] + [Query] + [Instructions: "context is in variable `text`"]
→ Model writes: text[0:1000]  # peek at start
→ Model writes: re.findall(r'pricing.*', text)  # search for specifics
→ Model writes: llm_query("summarize this chunk", text[5000:8000])  # recursive call
→ Model assembles final answer from targeted extractions
```

---

## Performance Results

- **2× improvement** over base models on long-context tasks
- Successfully processes inputs **100× beyond model context windows** (10M+ tokens)
- 80% accuracy on 60k-token datasets vs 0% for direct API calls
- Uses ~2-3k tokens per query vs 95k+ for standard methods
- 33% performance improvement over baseline approaches

### Benchmarks
- S-NIAH (needle in a haystack)
- BrowseComp-Plus (web browsing comprehension)
- OOLONG / OOLONG-Pairs (long-context aggregation)

---

## Implementation Details

### GitHub: ysz/recursive-llm

**Dependencies:**
- Python 3.9+
- LiteLLM (supports 100+ providers: OpenAI, Anthropic, Ollama, etc.)
- RestrictedPython (safe REPL execution)

**Configuration Options:**
- `max_depth`: Recursion depth limits
- `max_iterations`: REPL iteration threshold
- `recursive_model`: Can use cheaper model for recursive sub-calls
- Async API via `acomplete()`

**Code Structure:**
```
recursive-llm/
├── completion.py      # Async completion logic for recursive operations
├── repl_executor.py   # RestrictedPython REPL for safe code evaluation
├── prompt_builder.py  # Prompt construction
├── answer_parser.py   # Result extraction
└── config.py          # Model and recursion settings
```

### Basic Usage
```python
from recursive_llm import acomplete

result = await acomplete(
    model="claude-sonnet-4-20250514",
    query="What are the key pricing gaps for this business?",
    context=full_research_output,  # Can be 100k+ tokens
    max_depth=3,
    recursive_model="claude-haiku-4-5-20251001"  # Cheaper model for sub-calls
)
```

---

## Advanced Patterns

### Sub-LLM Parallelization
```python
# llm_batch() runs multiple queries in parallel
results = llm_batch([
    ("Extract pricing info", chunk_1),
    ("Extract competitor names", chunk_2),
    ("Extract review highlights", chunk_3)
])
```

### Answer Management Pattern
```python
# Iterative refinement using answer dictionary
answer = {"content": "", "ready": False}
# Model gradually builds answer across multiple REPL iterations
# Enables refinement rather than single-shot outputs
```

### Emergent Strategies (models discover these without instruction)
- **Filtering via regex** — `re.findall(pattern, text)` to find relevant sections
- **Semantic chunking** — Breaking text into meaningful segments
- **Answer verification** — Sub-calls to verify extracted information
- **Variable stitching** — Combining outputs from multiple recursive calls

---

## Integration Pattern for Titan Pipeline

### Current Flow (lossy):
```
lead_discovery → DB row → lead_research → DB row → email_compose
                 ↑ context lost here      ↑ context lost here
```

### RLM-Enhanced Flow:
```
lead_discovery → DB row + full output to Mem0
lead_research → DB row + full output to Mem0
email_compose → queries Mem0 for full context → writes personalized email
```

### Implementation in email_compose.py:
```python
# Instead of:
lead = await db.get_lead(lead_id)
email = await llm.compose_email(lead.name, lead.industry, lead.gap_score)

# Do this:
lead = await db.get_lead(lead_id)
research_context = await mem0.get_full_research(lead_id)  # Full research output
email = await recursive_compose(
    query=f"Write a personalized cold email for {lead.name}",
    context=research_context,  # Full research: reviews, pricing, competitors
    max_depth=2,
    recursive_model="haiku"  # Cheap model for context exploration
)
```

---

## Constraints

- Sequential REPL execution (no parallelization within a single query yet)
- No prefix caching implemented
- Recursion depth must be bounded
- Streaming unsupported currently
- Performance gains require explicit training for maximum benefit
- REPL output limited to 8,192 characters per turn (forces strategic filtering)

---

## Cost Analysis

Despite higher completion tokens for reasoning, the reduction in main-model token consumption often yields **comparable or lower total costs**:
- Main model processes 2-3k tokens instead of 95k+
- Sub-calls use cheaper models (Haiku at $0.25/M input vs Opus at $15/M)
- Net cost roughly equivalent to single large-context call

---

## Sources

- [ArXiv Paper](https://arxiv.org/abs/2512.24601)
- [Full HTML Paper](https://arxiv.org/html/2512.24601v1)
- [GitHub Implementation](https://github.com/ysz/recursive-llm)
- [Prime Intellect Deep Dive](https://www.primeintellect.ai/blog/rlm)
- [VentureBeat Coverage](https://venturebeat.com/orchestration/mits-new-recursive-framework-lets-llms-process-10-million-tokens-without)
- [InfoQ Summary](https://www.infoq.com/news/2026/01/mit-recursive-lm/)
