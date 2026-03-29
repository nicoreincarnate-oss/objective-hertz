---
name: perseus-revenue
description: Show revenue stats — total earned, pending invoices, MRR, deal history.
version: 1.0.0
triggers:
  - revenue
  - how much money
  - earnings
  - invoices
  - deals
  - MRR
---

# Perseus Revenue

Show Nico the money. Revenue collected, pending invoices, monthly recurring revenue.

## Instructions

Use two sources and combine them:

1. **GET /api/health** on the Hermes dashboard (http://localhost:8000) — returns `metrics` with:
   - `revenue_cleared` — total from `deals` where `status = 'paid'`
   - `revenue_pending` — total from `deals` where `status = 'pending'`
   - `sales_closed` — count of closed/building/deployed/invoiced/paid clients
   - `pending_approvals` — items waiting in the review queue
   - `warm_leads`, `total_leads`, `emails_sent_today`, `emails_sent_week`

2. **Titan A2A server** (http://localhost:9001) with capability `budget_status` — returns:
   - **Spend this month** — `budget_tracking` table grouped by `category`
   - **Monthly budget cap and remaining headroom**

Note: `/api/pipeline` only returns client status counts (discovered/contacted/interested/closed),
not revenue figures. Use `/api/health` for all money-related metrics.

- **Active hosting MRR** — query `hosting_subscriptions` via Titan `pipeline_status` or DB
- **Active receptionist MRR** — query `receptionist_subscriptions` via Titan `pipeline_status` or DB

Keep it about the numbers. Nico wants to see money, not explanations.
