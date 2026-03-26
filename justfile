# Perseus Agentic Engineering Command Launcher
# Usage: just <command>
# NOTE: Makefile handles infrastructure. Justfile handles agentic operations.
# SCOPE: Optional Claude Code helper commands. Not the canonical way to
# operate the system — use Makefile targets or direct daemon commands
# for production operations. These are convenience shortcuts for
# development sessions.

# Default: show available commands
default:
    @just --list

# Start a Claude Code session with prime context
cli:
    claude --print-prompt .claude/commands/prime.md

# Run full project setup (interactive)
setup:
    claude --prompt "/install"

# Run maintenance cycle
maintain:
    bash .claude/scripts/maintenance.sh 2>&1 | tee logs/maintenance_$(date +%Y%m%d_%H%M%S).log
    claude --prompt "Read logs/maintenance_*.log (most recent) and report findings. Fix any issues found."

# Run full quality checks (ruff + mypy + pytest)
test:
    claude --prompt "/test_backend"

# Plan a feature
plan FEATURE:
    claude --prompt "/plan {{FEATURE}}"

# Build from latest plan
build:
    claude --prompt "/build"

# Review current changes
review:
    claude --prompt "/review"

# Full system health check
health:
    claude --prompt "/health-check"

# Show damage control status
safety:
    claude --prompt "/damage-control"

# Test specific daemon
test-titan:
    claude --prompt "/test_titan"

test-hermes:
    claude --prompt "/test_hermes"

# Full Perseus health (daemons + docker + db + code)
health-full:
    claude --prompt "/health_perseus"

# Run parallel agents (P thread)
parallel PROMPT COUNT="4":
    @for i in $(seq 1 {{COUNT}}); do \
        echo "Spawning agent $i..."; \
        claude --prompt "{{PROMPT}}" & \
    done; \
    wait; \
    echo "All agents complete."

# Orchestrator mode
orchestrate TASK:
    claude --prompt "You are the orchestrator agent. Read .claude/agents/orchestrator.md for your instructions. Task: {{TASK}}"

# Context audit
context:
    claude --prompt "/context_audit"

# Reproduce a bug
bug DESCRIPTION:
    claude --prompt "/reproduce_bug {{DESCRIPTION}}"
