# Phase 5: Firecrawl Sync-to-Async Migration

## Goal
Convert `tools/firecrawl_client.py` from blocking `requests` to async `httpx.AsyncClient`, unblocking the event loop during web scraping operations.

## Tasks

### 5.1 Audit all firecrawl_client callers
- Run: `git grep -n "firecrawl_client\|from tools.firecrawl\|import firecrawl" --include="*.py"`
- For each caller, verify:
  - Is the caller already async? (Must be for this migration)
  - Does any test call firecrawl synchronously? (Would break)
  - Are there any module-level calls? (Would break)
- Expected callers: `titan/pipeline/lead_discovery.py`, `titan/pipeline/lead_research.py`, possibly `clawdbot/`
- **BLOCKER if any synchronous callers found**: Must wrap with asyncio.run() or defer migration

### 5.2 Convert firecrawl_client.py to async
- Replace `import requests` with `import httpx`
- Convert `_post()` to `async def _post()` using `httpx.AsyncClient`
- Convert all public functions to async:
  - `search_firecrawl()` → `async def search_firecrawl()`
  - `crawl_firecrawl()` → `async def crawl_firecrawl()`
  - `scrape_firecrawl()` → `async def scrape_firecrawl()`
- httpx API mapping:
  - `requests.post(url, json=data, headers=h, timeout=t)` → `await client.post(url, json=data, headers=h, timeout=t)`
  - `response.json()` → `response.json()` (same)
  - `response.status_code` → `response.status_code` (same)
  - `response.raise_for_status()` → `response.raise_for_status()` (same)
- Preserve all existing error handling, retry logic, and response parsing

### 5.3 Update all callers
- For each caller found in 5.1:
  - Add `await` to firecrawl function calls
  - Verify the surrounding function is already `async def`
- Expected change per caller: `result = scrape_firecrawl(url)` → `result = await scrape_firecrawl(url)`

### 5.4 Update tests
- Find all test files that import firecrawl_client
- Update test functions to be async (add `async def` + `@pytest.mark.asyncio`)
- Update mock targets from `requests.post` to `httpx.AsyncClient.post`
- Verify all test assertions still pass

## Rules
- **NEVER change response parsing logic** — only change how HTTP calls are made
- **NEVER change error handling** — same exceptions, same retries, same fallbacks
- **NEVER change function signatures** beyond adding async — same parameters, same return types
- **NEVER add new dependencies** — httpx is already in pyproject.toml (used by shared/llm_client.py)
- Preserve all: timeouts, headers, authentication, URL construction, rate limiting

## Validation
- [ ] `git grep firecrawl_client` — every caller uses `await`
- [ ] No synchronous callers remain
- [ ] `ruff check` passes
- [ ] `PYTHONPATH=. python3 -m pytest tests/ -x` — full suite passes
- [ ] Integration test: mock httpx responses, verify pipeline stage completes
- [ ] Verify `requests` is no longer imported in firecrawl_client.py
- [ ] Verify all firecrawl tests pass with async mocks

## LOC Impact
- Modified: `tools/firecrawl_client.py` (~30 LOC changed)
- Modified: caller files (~5 LOC each, ~3-5 files)
- Modified: test files (~20 LOC changed)
- Net: **~50 LOC changed** (no net addition/removal)

## Risk: MEDIUM
- Every caller must be updated — missing an `await` is a runtime bug (returns coroutine instead of result)
- Test mock targets change from `requests` to `httpx`
- Mitigation: exhaustive caller audit in 5.1, full test suite run
- httpx API is nearly identical to requests, minimizing translation errors
