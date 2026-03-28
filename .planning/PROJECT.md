# Objective Hertz — Launch Readiness Plan

## Goal
Get from "code complete with blockers" to "collecting revenue" in 75 days. First paying customer by Day 35. $8K MRR by Day 75.

## System Overview
Autonomous AI revenue pipeline: discovers local businesses without websites, sends outreach emails, builds sites, deploys to Netlify, collects payment via Stripe. 5 daemons (Perseus orchestrator, Titan pipeline, Hermes alerts/dashboard, ClawdBot site builder, Conway economics) running on Mac Studio M4 32GB.

## Constraints
- **Budget**: $800/month total (Claude API ~$200-400, Instantly ~$97, Firecrawl ~$16, Stripe ~3% of revenue)
- **Hardware**: Mac Studio M4 32GB arrives 3/31. ~22GB RAM needed, ~10GB headroom.
- **Warmup**: Instantly email warmup takes 14-21 days. Day 1 cap = 10 emails. Day 7 = 50. Day 21 = 600.
- **Operator**: Single operator (Nico) approves first 10 sales manually. Telegram + War Room dashboard.
- **Target market**: Austin TX, home services (plumbers, electricians, HVAC, landscapers).

## Key Decisions (from Alpha-Beta Debate)
1. Warmup starts Day 1 — parallel with code fixes, not sequential
2. Stripe replaces Wise entirely for payment collection — Wise creates outbound transfers, not invoices
3. Shadow mode gets 5 days (not 4 or 7) — enough to find bugs, not so long it delays revenue
4. Single city (Austin) first — prove one market before expanding
5. Manual sales for first 10 customers — Nico handles every conversation
6. Conway economics deferred until $10K MRR — adds complexity with no revenue benefit now
7. MAGMA learning deferred until Phase 7 — no data to learn from yet
8. Decision gate at Day 35 — if zero positive replies, pivot vertical before expanding cities

## Critical Bugs Found (Pre-Launch)
- **P0**: No /unsub endpoint (CAN-SPAM violation, $50K/email)
- **P0**: Wise payment creates OUTBOUND transfers (sends YOUR money out)
- **P0**: Compliance config not seeded (company_address, unsubscribe_base_url, UNSUBSCRIBE_SECRET)
- **P0**: War Room "Approve All" does raw SQL but never triggers email dispatch via Instantly
- **P0**: Instantly accounts need warmup (operational, not code)
- **P1**: No Stripe webhook (2-hour polling delay for payment confirmation)
- **P1**: Review mode has API but no dedicated approval UI
- **P1**: No recurring billing for $52/mo hosting or $398/mo receptionist

## Success Metrics
- Day 7: All P0 blockers resolved. Warmup running.
- Day 14: Mac Studio stable. 100+ pre-qualified leads identified.
- Day 19: Shadow mode complete. Zero critical errors.
- Day 21: First real emails sent.
- Day 35: First paying customer. Decision gate: continue vertical or pivot?
- Day 60: 10+ customers, $3K MRR, <2hr/day operator time.
- Day 75: 30+ customers, $8K MRR, <1hr/day operator time.
