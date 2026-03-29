## Current Turn: auditor
## Cycle: 23

### Scope
Files allowed this cycle:
- /Users/majovega/Desktop/Projects/objective-hertz/titan/pipeline/email_send.py
- /Users/majovega/Desktop/Projects/objective-hertz/titan/pipeline/follow_up.py
- /Users/majovega/Desktop/Projects/objective-hertz/scripts/init-db.sql
- /Users/majovega/Desktop/Projects/objective-hertz/tests/test_email_campaign_setup.py

### Fixes Applied (fixer — Cycle 22)

**Finding 1 (P1) — email_sequences.status still set to 'sent' on queue acceptance**
- Files: `scripts/init-db.sql` (email_sequences CHECK), `titan/pipeline/email_send.py` (line ~382), `titan/pipeline/follow_up.py` (line ~113)
- Fix: Three changes:
  1. Schema: Added `'queued'` to `email_sequences.status` CHECK constraint: `('pending', 'queued', 'sent', 'opened', 'replied', 'bounced')`.
  2. `_add_lead_to_campaign()`: Changed `SET status = 'sent', sent_at = NOW()` to `SET status = 'queued'`. No `sent_at` stamp on queue acceptance — that gets set by analytics sync when Instantly confirms actual delivery.
  3. `follow_up.py` `_process_reply()`: Updated reply attribution query from `WHERE status = 'sent'` to `WHERE status IN ('queued', 'sent', 'opened')` so replies are attributed whether the email was queued, confirmed sent, or already opened. Also changed `ORDER BY sent_at DESC` to `ORDER BY created_at DESC` since queued sequences won't have `sent_at` yet.
- Test: Updated `test_add_lead_sets_email_queued_not_email_sent` to assert the sequence UPDATE uses `'queued'` (not `'sent'`) AND the client UPDATE uses `'email_queued'` (not `'email_sent'`).

### Verification (fixer)
1. `.venv/bin/python -m pytest tests/test_discovery_source_selection.py tests/test_email_campaign_setup.py tests/test_local_parallel_research.py tests/test_follow_up_queue.py tests/test_follow_up_replies.py tests/test_email_send_followups.py -v` → **13 passed, 0 failed**
2. The full delivery honesty chain is now: `pending` → `queued` (accepted by Instantly) → `sent` (confirmed by analytics) → `opened`/`replied`/`bounced`.

### Verification (auditor)
1. Re-checked the production path:
   - `titan/pipeline/email_send.py` now writes `email_sequences.status = 'queued'` with no premature `sent_at` stamp on queue acceptance.
   - `titan/pipeline/follow_up.py` now attributes replies across `queued`, `sent`, and `opened` sequence states.
   - `scripts/init-db.sql` now allows `'queued'` in the `email_sequences.status` constraint, so the code and schema agree.
2. Re-ran the outreach slice:
   - `.venv/bin/python -m pytest tests/test_discovery_source_selection.py tests/test_email_campaign_setup.py tests/test_local_parallel_research.py tests/test_follow_up_queue.py tests/test_follow_up_replies.py tests/test_email_send_followups.py -q`
   - Result: `13 passed`
3. No surviving finding in this round.
