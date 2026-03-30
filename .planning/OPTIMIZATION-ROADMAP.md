# Code Optimization Roadmap

## Phase 1: Dead Code Removal + Lazy Import Fix
**Goal**: Remove confirmed dead code and fix import ordering in orchestrator hot loops.
**Success**: -4,750 LOC removed. Zero import errors. Full test suite passes.

## Phase 2: LLM Cost Estimation Fix + Slow Query Logging
**Goal**: Fix budget tracking accuracy and add database observability.
**Success**: budget_tracking uses real API token counts. Slow queries (>500ms) emit warnings.

## Phase 3: Silent Exception Hardening
**Goal**: Make all 20+ silent exception locations observable without changing control flow.
**Success**: Zero `except...pass` in production hot paths. All exceptions logged.

## Phase 4: JSON Extraction Consolidation (Reduced Scope)
**Goal**: Create shared/json_utils.py and migrate 10-12 simple identical call sites.
**Success**: New utility tested. Migrated sites produce identical outputs. Complex sites documented.

## Phase 5: Firecrawl Sync-to-Async Migration
**Goal**: Convert firecrawl_client.py from blocking requests to async httpx.
**Success**: All firecrawl calls async. Event loop no longer blocked during scraping.
