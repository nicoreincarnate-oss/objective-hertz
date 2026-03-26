---
name: perseus-approve
description: Approve or reject items in the Perseus review queue (emails, proposals, sites).
version: 1.0.0
triggers:
  - approve
  - reject
  - review queue
  - pending approvals
  - show me what needs approval
---

# Perseus Review Approval

Manage the review queue for Perseus. During the first 10 sales, Nico approves emails, proposals, and demo sites before they go out.

## Instructions

When the user asks to review, approve, or reject:

1. Call the Titan A2A server (http://localhost:9001) with capability `pipeline_status` to surface
   pending review items from the `review_queue` table (status = 'pending_review').
2. Show each pending item with its ID, type (email_draft / proposal), and content preview.
3. When the user says "approve X" or "reject X":
   - Call Titan A2A capability `review_approve` with `{"review_id": X, "notes": "..."}` to approve.
   - Call Titan A2A capability `review_reject` with `{"review_id": X, "notes": "..."}` to reject.
   - Confirm the action was taken based on the `{"success": true, "review_id": X}` response.

Note: approval routing goes through the Titan A2A server (port 9001), not a Hermes HTTP endpoint.
The Hermes Telegram bot uses `call_agent_capability("titan", "review_approve" / "review_reject", ...)`
for the same path.

After 10 sales are approved, review mode automatically disables and Titan becomes fully autonomous.
