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

1. Query http://localhost:8500/api/events to check pending review items
2. Show each pending item with its ID, type, and content preview
3. When the user says "approve X" or "reject X":
   - POST to the appropriate endpoint to update the review queue
   - Confirm the action was taken

After 10 sales are approved, review mode automatically disables and Titan becomes fully autonomous.
