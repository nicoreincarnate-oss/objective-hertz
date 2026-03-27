---
description: Create a structured implementation plan for a feature or task
---

# Plan Command

## Purpose
Create a detailed, structured plan before building anything in the Perseus system.

## Variables
- $TASK: What needs to be planned (provided by user)

## Instructions
1. Analyze the task requirements
2. Identify which daemons are affected (Perseus/Titan/Hermes/ClawdBot)
3. Break down into discrete, actionable steps
4. Identify dependencies between steps
5. Flag risks (especially around payments, emails, and autonomous operations)
6. Define success criteria
7. Estimate complexity (small/medium/large)

## Workflow
1. Read CLAUDE.md for project context
2. Check specs/ for any related existing plans
3. Check .claude/plans/ for historical plans
4. Create plan document at specs/plan_[feature_name].md
5. Structure the plan:
   - Goal and non-goals
   - Daemons affected
   - Steps (numbered, with dependencies noted)
   - Risks and mitigations (especially financial/email risks)
   - Success criteria
   - Estimated scope
6. Present summary to user for approval before building

## Output
Write plan to specs/ directory. Present summary in conversation.

## Rules
- Do NOT start building during planning
- Do NOT skip the risk assessment
- Always check for existing related plans first
- Keep steps atomic and testable
- Flag any changes that touch payment or email flows
