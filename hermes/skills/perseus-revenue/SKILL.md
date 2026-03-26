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

Query the Titan A2A server (http://localhost:9001) with capability `pipeline_status` and
`budget_status`, then present data drawn from the actual database tables:

- **Total revenue collected** — `deals` table, `amount` where `status = 'paid'`
- **Pending invoices** — `deals` table, `amount` where `status = 'pending'`
- **Number of deals closed** — count of `deals` rows with `status = 'paid'`
- **Active hosting MRR** — `hosting_subscriptions` table, `monthly_price` where `status = 'active'`
- **Active receptionist MRR** — `receptionist_subscriptions` table, `monthly_price` where `status = 'active'`
- **Pipeline stages** — `clients` table, `status` column (discovered → contacted → interested → closed)
- **Spend this month** — `budget_tracking` table, `amount` for current month grouped by `category`
- **This month vs last month comparison** if data available via `budget_tracking.month`

Keep it about the numbers. Nico wants to see money, not explanations.
