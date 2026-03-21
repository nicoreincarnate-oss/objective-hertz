# Perseus Research Findings (March 2026)

## Cold Email Platform: Instantly.ai (Recommended)

**Winner: Instantly.ai Hypergrowth ($97/mo)**

| Feature | Instantly.ai | Smartlead | Saleshandy | Lemlist |
|---------|-------------|-----------|------------|---------|
| Price | $97/mo | $94/mo | $69/mo | $69/mo |
| Monthly emails | 100,000 | 150,000 | 150,000 | Unlimited |
| Email accounts | Unlimited | Unlimited | Unlimited | 3 |
| Warm-up | Yes (API) | Yes (API) | Yes | Yes |
| API quality | Excellent | Good | OK + MCP | UI-first |
| Webhooks | Yes | Yes | Yes | Yes |
| Mexico OK | Yes | Yes | Yes | Yes |

**Why Instantly:** Best API docs, webhook-driven (ideal for Titan's autonomous operation), unlimited accounts on flat fee, 100K emails/mo covers 1000/day with headroom.

**Runner-up: Smartlead** — More emails, good API, slightly less polished docs.

**Saleshandy** worth considering if MCP integration proves valuable.

## Payment Platform: Stripe Mexico + Wise Business

**For receiving international payments to Mexican bank (MXN):**

| Platform | International Receive | MXN Payout | API | Fees |
|----------|----------------------|------------|-----|------|
| Stripe Mexico | Yes | Yes | Excellent | 3.6% + $3 MXN |
| Wise Business | Yes | Yes | Good | ~1% conversion |
| PayPal Mexico | Yes | Yes (slow) | OK | 3.4-4.4% + fixed |
| Mercado Pago | Limited | Yes | OK | For LATAM mainly |

**Recommendation: Use both Stripe Mexico (for card payments) and Wise Business (for wire transfers).** Stripe handles the payment link/invoice, Wise handles international transfers.

## Website Builder API: v0 by Vercel (Recommended)

**Bolt.new does NOT have a public API.** It's browser-only.

| Platform | Public API | Headless | Best For |
|----------|-----------|----------|----------|
| **v0 by Vercel** | YES (beta) | YES, REST + SDK | Programmatic site generation |
| Bolt.new | No | No | Interactive browser use |
| Lovable.dev | Partial | Semi | Embedding in workflows |
| bolt.diy | Self-host | DIY | Custom with any LLM |

**Winner: v0 Platform API (Vercel)**
- Send a prompt → get production React code (shadcn/ui + Tailwind)
- Auto-deploy to Vercel with custom subdomains
- REST API + TypeScript SDK
- Free tier: 200 credits/month, paid from $10/mo
- Already used by others to build website builders, Slack bots, CRM integrations

**This means Titan should use v0.dev API instead of Bolt.new for site building.**

## Action Items

1. Sign up for Instantly.ai Hypergrowth ($97/mo)
2. Set up Stripe Mexico account
3. Get v0.dev API access (free tier to start)
4. Update `titan/pipeline/build_site.py` to use v0 API instead of Bolt.new
5. Update `tools/smartlead_client.py` to support Instantly.ai API (or create `tools/instantly_client.py`)
