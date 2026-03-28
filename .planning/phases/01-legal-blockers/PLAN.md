# Phase 1: Legal Blockers + Warmup Start (Days 1-7)

**Goal**: Fix all P0 blockers so the system can legally send email and collect payment. Begin Instantly warmup. Begin Mac Studio setup when it arrives (Day 4).

**Success Criteria**:
- `titan/compliance.py` `assert_compliance_ready()` returns zero issues
- `/unsub` endpoint responds to GET with valid HMAC token, returns confirmation HTML
- Stripe payment link creation works with test $1 amount
- War Room "Approve All" triggers actual email dispatch via `approve_review()`
- Instantly warmup active and progressing
- mail-tester.com score 8+/10

---

## Task 1: Build /unsub Endpoint (CAN-SPAM Requirement)

**Priority**: P0 -- legally required before any outbound email
**Estimated effort**: 3-4 hours
**Dependencies**: None (can start immediately)

### Files to modify

**`hermes/web/app.py`**

1. Add `/unsub` to `_PUBLIC_PATHS` on line 43:
   ```python
   _PUBLIC_PATHS = {"/api/liveness", "/metrics", "/login", "/unsub"}
   ```

2. Add a new GET endpoint `/unsub` with the following logic:
   - Read query params `id` (client_id, integer) and `sig` (HMAC signature, string)
   - Validate both are present; return 400 HTML if missing
   - Load `UNSUBSCRIBE_SECRET` from `os.getenv("UNSUBSCRIBE_SECRET", "")`
   - Recompute the expected HMAC using the same algorithm as `titan/compliance.py` line 53-55:
     ```python
     expected = hmac.new(
         secret.encode(), str(client_id).encode(), hashlib.sha256
     ).hexdigest()[:32]
     ```
   - Compare using `hmac.compare_digest(sig, expected)` -- return 403 HTML if mismatch
   - Update client status in DB:
     ```sql
     UPDATE clients SET status = 'unsubscribed', updated_at = NOW() WHERE id = %s
     ```
   - Log the unsubscribe event:
     ```python
     await emit_event("client_unsubscribed", {"client_id": client_id, "source": "unsub_link"})
     ```
   - Sync with Instantly suppression list (see step 3 below)
   - Return confirmation HTML page (simple, branded, says "You have been unsubscribed")

3. Add Instantly suppression sync inside the `/unsub` handler, after DB update:
   ```python
   try:
       client_row = await fetch_one("SELECT email FROM clients WHERE id = %s", (client_id,))
       if client_row:
           from tools.instantly_client import InstantlyClient
           ic = InstantlyClient()
           await ic._post("/leads/blocklist", {"email": client_row["email"]})
           await ic.close()
   except Exception as e:
       logger.warning("Failed to sync unsub to Instantly blocklist: %s", e)
   ```
   NOTE: Check the Instantly v2 API docs for the exact blocklist/suppression endpoint. The path `/leads/blocklist` is a placeholder -- it may be `/leads/delete` or a campaign-level removal. Verify against https://developer.instantly.ai/ before implementing.

### How to test

1. **Unit test**: Create `tests/test_unsub_endpoint.py`
   - Mock DB and test valid HMAC returns 200 + HTML with "unsubscribed"
   - Test invalid HMAC returns 403
   - Test missing params returns 400
   - Test already-unsubscribed client still returns 200 (idempotent)

2. **Integration test**: With a running Hermes web server
   ```bash
   # Generate a valid link using the compliance module
   python3 -c "
   import hmac, hashlib
   secret = 'YOUR_TEST_SECRET'
   client_id = 1
   sig = hmac.new(secret.encode(), str(client_id).encode(), hashlib.sha256).hexdigest()[:32]
   print(f'http://localhost:8000/unsub?id={client_id}&sig={sig}')
   "
   # Visit the URL in a browser -- should show confirmation page
   # Verify: SELECT status FROM clients WHERE id = 1; -- should be 'unsubscribed'
   ```

3. **Negative test**: Tamper with the `sig` parameter -- should get 403

---

## Task 2: Seed Compliance Config

**Priority**: P0 -- `assert_compliance_ready()` blocks Titan startup without this
**Estimated effort**: 2-3 hours
**Dependencies**: Task 1 must be deployed (need the public URL for `unsubscribe_base_url`)

### Files to modify

**`.env`** (operator action -- Claude cannot edit this file)

Add or update:
```
UNSUBSCRIBE_SECRET=<generate with: python3 -c "import secrets; print(secrets.token_urlsafe(48))">
```
Must be 16+ characters and NOT any of the placeholder values listed in `titan/compliance.py` lines 76-85 (`changeme`, `secret`, `test`, etc.).

**`scripts/init-db.sql`** -- No changes needed (defaults are intentional placeholders). The real values go in via SQL after deployment.

**Database** (run manually or via migration script):

```sql
-- Set real company address (CAN-SPAM requires physical address)
UPDATE system_config
SET value = '"Your Real Business Address, City, State ZIP"',
    is_customized = TRUE,
    updated_at = NOW()
WHERE key = 'company_address';

-- Set the public-facing Hermes URL where /unsub lives
UPDATE system_config
SET value = '"https://your-public-hermes-domain.com"',
    is_customized = TRUE,
    updated_at = NOW()
WHERE key = 'unsubscribe_base_url';
```

Create a migration script at **`scripts/migrations/001_seed_compliance.sql`** with the above SQL (using placeholder values that the operator fills in).

**`titan/daemon.py`** -- Make compliance check truly fatal

The current code on lines 145-148 catches the `RuntimeError` from `assert_compliance_ready()` and logs it as a non-fatal warning:
```python
try:
    from titan.compliance import assert_compliance_ready
    await assert_compliance_ready()
except Exception as e:
    logger.warning("Compliance readiness check failed (non-fatal): %s", e)
```

Change to:
```python
from titan.compliance import assert_compliance_ready
await assert_compliance_ready()
# No try/except -- if compliance is misconfigured, Titan must NOT start.
# The RuntimeError from assert_compliance_ready() will propagate and
# prevent the daemon from entering its main loop.
```

This is the single most important change in this task. If Titan starts despite compliance failures, emails go out without unsubscribe links (CAN-SPAM violation).

### How to test

1. **Without config**: Start Titan with placeholder values in system_config and no `UNSUBSCRIBE_SECRET` env var. Titan must crash with `RuntimeError` listing the missing items.

2. **With config**: Set all three values correctly. Titan must start normally.

3. **Partial config**: Set address but not secret. Titan must crash listing only the missing secret.

4. **Validation**:
   ```python
   # In a Python shell with DB connection:
   from titan.compliance import get_compliance_issues
   import asyncio
   issues = asyncio.run(get_compliance_issues())
   assert issues == [], f"Compliance issues remain: {issues}"
   ```

---

## Task 3: Disable Wise Payment Creation

**Priority**: P0 -- Wise creates real bank transfers; without Stripe as sole path, we risk orphan artifacts
**Estimated effort**: 1-2 hours
**Dependencies**: None (can start immediately)

### Files to modify

**`tools/payment_router.py`**

1. **`create_invoice()` method (line 45-67)**: Remove Wise as a creation fallback. After the Stripe block (line 58-61), instead of falling through to `_create_wise_invoice`, fail explicitly:

   Current code (lines 58-67):
   ```python
   if self._stripe_available:
       return await self._create_stripe_invoice(...)
   if self._wise_available:
       return await self._create_wise_invoice(...)
   logger.warning("No payment provider available")
   return {"reference": "", "url": "", "provider": "none"}
   ```

   Change to:
   ```python
   if self._stripe_available:
       return await self._create_stripe_invoice(...)
   # Wise is CHECK-ONLY -- never create invoices/transfers via Wise.
   # If Stripe is not configured, fail loudly so the operator knows.
   logger.error(
       "STRIPE_API_KEY is not configured. Cannot create invoice. "
       "Wise is available for payment CHECKING only, not invoice creation."
   )
   return {"reference": "", "url": "", "provider": "none", "error": "Stripe not configured"}
   ```

2. **`get_status()` method (lines 28-43)**: Update the status report to reflect that Wise is check-only. When only Wise is available (no Stripe), return a `blocked` status for invoice creation:

   Change the Wise branch (lines 33-35) to:
   ```python
   if self._wise_available:
       return truth_payload("degraded", "wise_check_only", True,
                            summary="Wise is available for payment reconciliation only. Stripe required for invoicing.",
                            provider="wise")
   ```

3. **Keep `_check_wise_payments()` and `check_new_payments()` unchanged** -- Wise payment checking is still needed for manual reconciliation of bank transfers.

4. **Keep `_create_wise_invoice()` method in the file** but add a deprecation docstring:
   ```python
   async def _create_wise_invoice(self, ...):
       """DEPRECATED: Wise invoice creation is disabled in production.
       Kept for reference only. See payment_router.py Task 3 in Phase 1 plan.
       """
       raise RuntimeError("Wise invoice creation is disabled. Use Stripe.")
   ```

### How to test

1. **Without Stripe key**: Instantiate `PaymentRouter()` with no `STRIPE_API_KEY`. Call `create_invoice()`. Must return `{"error": "Stripe not configured"}` and NOT attempt a Wise transfer.

2. **With Stripe key**: Call `create_invoice()` with Stripe test key. Must create a Stripe payment link.

3. **Wise check still works**: With `WISE_API_TOKEN` set, `check_new_payments()` must still query Wise for incoming transfers.

4. **Status check**: `get_status()` with only Wise configured must return `"degraded"`, not `"live"`.

5. **Add test**: `tests/test_payment_router_no_wise_create.py`
   ```python
   async def test_create_invoice_without_stripe_fails():
       router = PaymentRouter()
       router._stripe_available = False
       router._wise_available = True
       result = await router.create_invoice("test@example.com", "Test", 100.0)
       assert result["error"] == "Stripe not configured"
       assert result["provider"] == "none"
   ```

---

## Task 4: Fix War Room "Approve All" Button

**Priority**: P0 -- without this, approved emails sit in the queue and never send
**Estimated effort**: 2-3 hours
**Dependencies**: Task 2 (compliance config must be seeded or Instantly sends will fail)

### The Bug

`hermes/web/app.py` lines 911-922: The `/api/review/bulk` endpoint with `action=approve_all` does a raw SQL UPDATE:
```python
await fetch_val(
    "UPDATE review_queue SET status = 'approved' "
    "WHERE status = 'pending_review' RETURNING COUNT(*)"
)
```
This marks items as `approved` in the database but never calls `approve_review()` from `titan/review_mode.py`, which is the function that actually triggers `send_to_instantly()` to dispatch the email.

Result: Emails show as "approved" in the War Room but never send.

### Files to modify

**`hermes/web/app.py`** -- Replace the bulk approve block (lines 911-922)

Current code:
```python
if action == "approve_all":
    await fetch_val(
        "UPDATE review_queue SET status = 'approved' "
        "WHERE status = 'pending_review' RETURNING COUNT(*)"
    )
    count = await fetch_val(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'approved' "
        "AND updated_at > NOW() - INTERVAL '5 seconds'"
    ) or 0
    await emit_event("bulk_approve", {"count": int(count), "source": "war_room"})
    return JSONResponse({"success": True, "action": "approve_all", "affected": int(count)})
```

Replace with:
```python
if action == "approve_all":
    from titan.review_mode import approve_review
    pending = await fetch_all(
        "SELECT id FROM review_queue WHERE status = 'pending_review' ORDER BY created_at ASC"
    )
    succeeded = 0
    failed = 0
    for row in pending:
        try:
            result = await approve_review(row["id"], notes="Bulk approved from War Room")
            if result:
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            logger.error("Bulk approve failed for review %s: %s", row["id"], e)
            failed += 1
    await emit_event("bulk_approve", {
        "count": succeeded, "failed": failed, "source": "war_room"
    })
    return JSONResponse({
        "success": True,
        "action": "approve_all",
        "affected": succeeded,
        "failed": failed,
    })
```

This loops through each pending review item and calls `approve_review()`, which internally calls `_send_approved_email()` or `_send_approved_proposal()`, both of which route through `send_to_instantly()` in the compliance module.

### Also check: Single-item approve endpoint

Search for a `/api/review/{id}/approve` or similar endpoint. If it exists, verify it also calls `approve_review()`. If it does not exist, the single-item approve from the War Room detail view is also broken and needs the same fix pattern.

Grep result showed no single-item approve endpoint in `app.py`. Check the frontend for how individual items are approved and add the endpoint if missing:

**`hermes/web/app.py`** -- Add single-item approve endpoint:
```python
@app.post("/api/review/{review_id}/approve")
async def api_review_approve(review_id: int, request: Request):
    """Approve a single review queue item and trigger send."""
    from titan.review_mode import approve_review
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    notes = body.get("notes", "")
    result = await approve_review(review_id, notes=notes)
    if result:
        return JSONResponse({"success": True, "review_id": review_id})
    return JSONResponse({"success": False, "error": "Approval failed"}, status_code=400)
```

### How to test

1. **Setup**: Ensure compliance config is seeded (Task 2), Instantly campaign exists, and at least one item is in `review_queue` with `status = 'pending_review'`.

2. **Test bulk approve**:
   ```bash
   curl -X POST http://localhost:8000/api/review/bulk \
     -H "Authorization: Bearer $DASHBOARD_SECRET" \
     -H "Content-Type: application/json" \
     -d '{"action": "approve_all"}'
   ```
   Expected: `{"success": true, "action": "approve_all", "affected": N, "failed": 0}`
   Verify: Check `outbound_email_log` table for new rows with `send_status = 'sent'`.
   Verify: Check Instantly dashboard for the newly added leads.

3. **Test single approve**:
   ```bash
   curl -X POST http://localhost:8000/api/review/42/approve \
     -H "Authorization: Bearer $DASHBOARD_SECRET" \
     -H "Content-Type: application/json" \
     -d '{"notes": "Looks good"}'
   ```

4. **Test with compliance failure**: Temporarily remove `UNSUBSCRIBE_SECRET` from env. Approve should fail with compliance error (not silently skip sending).

---

## Task 5: Configure DNS for Email Deliverability

**Priority**: P0 -- without SPF/DKIM/DMARC, emails land in spam
**Estimated effort**: 2-3 hours (mostly waiting for DNS propagation)
**Dependencies**: Instantly account must be active, sending domain must be registered

### Steps (Operator Actions -- not code changes)

1. **SPF Record**: Add a TXT record for the sending domain:
   ```
   Type: TXT
   Host: @
   Value: v=spf1 include:_spf.instantly.ai ~all
   ```
   If other services also send from this domain, merge them:
   ```
   v=spf1 include:_spf.instantly.ai include:_spf.google.com ~all
   ```

2. **DKIM Record**: Instantly provides DKIM keys during account setup.
   - Go to Instantly dashboard > Email Accounts > select account > DNS Settings
   - Copy the DKIM CNAME record(s)
   - Add them to DNS:
     ```
     Type: CNAME
     Host: <selector>._domainkey
     Value: <value from Instantly>
     ```

3. **DMARC Policy**: Add a TXT record:
   ```
   Type: TXT
   Host: _dmarc
   Value: v=DMARC1; p=none; rua=mailto:dmarc@yourdomain.com; pct=100
   ```
   Start with `p=none` (monitoring only). Move to `p=quarantine` after 2 weeks of clean reports.

4. **Custom tracking domain** (optional but recommended):
   ```
   Type: CNAME
   Host: track (or link)
   Value: <value from Instantly tracking domain setup>
   ```

### How to verify

1. **DNS propagation**: Wait 15-60 minutes after adding records. Check with:
   ```bash
   dig TXT yourdomain.com        # SPF
   dig CNAME sel._domainkey.yourdomain.com  # DKIM
   dig TXT _dmarc.yourdomain.com  # DMARC
   ```

2. **mail-tester.com**: Send a test email from Instantly to a mail-tester.com address. Target: 8+/10 score.

3. **MXToolbox**: Run SPF, DKIM, and DMARC checks at https://mxtoolbox.com/

4. **Instantly DNS check**: Instantly dashboard has a built-in DNS verification tool under Email Accounts > DNS.

---

## Task 6: Start Instantly Warmup (Day 1)

**Priority**: P0 -- warmup takes 14-21 days; starting late delays the entire launch
**Estimated effort**: 1 hour
**Dependencies**: Task 5 (DNS must be configured first for warmup to be effective)

### Steps

1. **Enable warmup via Instantly dashboard** or via API:
   ```python
   from tools.instantly_client import InstantlyClient
   client = InstantlyClient()
   result = await client.enable_warmup()
   print(result)  # Should return a background job ID
   await client.close()
   ```

2. **Verify warmup settings** in Instantly dashboard:
   - Daily ramp: Start at 2-5 emails/day, increase by 2-3/day
   - Reply rate target: 30%+
   - Warmup pool: Use Instantly's default warmup network

3. **Add warmup monitoring to Perseus scheduler** (optional automation):

   **`perseus/scheduler.py`** -- Add a daily warmup check task if not already present. This calls `get_warmup_analytics()` and logs the results so you can track warmup progression.

   ```python
   # Add to scheduled tasks dict:
   "warmup_check": {
       "handler": "titan.deliverability:check_warmup_progress",
       "interval_hours": 24,
       "description": "Check Instantly warmup analytics",
   }
   ```

   **`titan/deliverability.py`** -- Add (or verify exists):
   ```python
   async def check_warmup_progress():
       from tools.instantly_client import InstantlyClient
       client = InstantlyClient()
       try:
           analytics = await client.get_warmup_analytics()
           logger.info("Warmup analytics: %s", analytics)
           await db.emit_event("warmup_report", analytics)
       finally:
           await client.close()
   ```

### How to verify

1. **API check**:
   ```python
   client = InstantlyClient()
   analytics = await client.get_warmup_analytics()
   # Should show increasing sent/received counts day over day
   await client.close()
   ```

2. **Instantly dashboard**: Email Accounts tab should show warmup status as "Active" with daily progress bars.

3. **After 7 days**: Warmup volume should be at 20-40 emails/day per account. Check for any deliverability warnings.

---

## Execution Order

| Day | Task | Blocker? | Parallel? |
|-----|------|----------|-----------|
| 1 | Task 5: DNS configuration | Yes -- propagation takes hours | Start first |
| 1 | Task 6: Start Instantly warmup | Yes -- needs DNS first | After DNS verified |
| 1-2 | Task 1: Build /unsub endpoint | Yes -- CAN-SPAM | Can start Day 1 in parallel with DNS |
| 2 | Task 3: Disable Wise creation | Yes -- money safety | Independent, start anytime |
| 2-3 | Task 2: Seed compliance config | Yes -- blocks Titan start | Needs Task 1 deployed for base_url |
| 3-4 | Task 4: Fix Approve All button | Yes -- review mode is active | Needs Task 2 (compliance config) |
| 4-7 | Mac Studio setup | No | Parallel with final testing |

### Day-by-Day Schedule

**Day 1**:
- Morning: Configure DNS records (Task 5)
- Morning: Start coding /unsub endpoint (Task 1)
- Afternoon: Start Instantly warmup (Task 6) after DNS propagates
- Afternoon: Disable Wise payment creation (Task 3)

**Day 2**:
- Morning: Finish and test /unsub endpoint (Task 1)
- Afternoon: Deploy /unsub, get public URL
- Afternoon: Begin compliance config seeding (Task 2)

**Day 3**:
- Morning: Complete compliance config, make Titan startup fatal (Task 2)
- Morning: Verify `assert_compliance_ready()` returns zero issues
- Afternoon: Fix Approve All button (Task 4)

**Day 4**:
- Morning: Test full flow: compose email -> review queue -> approve -> Instantly send
- Afternoon: Mac Studio arrives -- begin setup
- Run mail-tester.com check (target 8+/10)

**Day 5-7**:
- Mac Studio setup and migration
- Monitor warmup progression
- End-to-end regression testing
- Fix any issues found during testing

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| Instantly API blocklist endpoint path is wrong | /unsub works locally but unsubs don't propagate to Instantly | Verify exact API path from Instantly v2 docs before implementing |
| DNS propagation slow (>24h) | Warmup delayed | Use low TTL, have DNS provider with fast propagation |
| Stripe test mode vs live mode confusion | Test passes but production fails | Use Stripe test key for testing, verify live key is set in production .env |
| Compliance check made fatal but config not yet seeded | Titan won't start | Seed config BEFORE deploying the fatal check. Or: deploy them in the same commit/deploy |
| Bulk approve overloads Instantly rate limit | Some emails fail to send | InstantlyClient already has 200ms rate limiting (line 37). Monitor for 429s during bulk. |
| Warmup accounts get flagged | Sending reputation damaged | Monitor warmup analytics daily, pause any flagged accounts immediately |

---

## Validation Checklist (All Must Pass Before Phase 2)

- [ ] `python3 -c "from titan.compliance import get_compliance_issues; import asyncio; print(asyncio.run(get_compliance_issues()))"` prints `[]`
- [ ] `curl https://your-domain.com/unsub?id=1&sig=VALID_SIG` returns 200 with HTML
- [ ] `curl https://your-domain.com/unsub?id=1&sig=BAD_SIG` returns 403
- [ ] Titan starts without compliance warning (check titan.log for absence of "non-fatal")
- [ ] Titan REFUSES to start when `UNSUBSCRIBE_SECRET` is removed from env
- [ ] `PaymentRouter().create_invoice(...)` with Stripe test key returns a payment link URL
- [ ] `PaymentRouter().create_invoice(...)` WITHOUT Stripe key returns error (no Wise attempt)
- [ ] War Room "Approve All" creates rows in `outbound_email_log` with `send_status = 'sent'`
- [ ] Approved email appears in Instantly campaign leads
- [ ] `dig TXT yourdomain.com` shows SPF record
- [ ] `dig TXT _dmarc.yourdomain.com` shows DMARC record
- [ ] Instantly dashboard shows warmup status: Active
- [ ] mail-tester.com score >= 8/10
- [ ] `ruff check titan/ hermes/ tools/` passes
- [ ] `PYTHONPATH=. python3 -m pytest tests/ -v` passes
