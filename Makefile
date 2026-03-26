.PHONY: help start stop status logs health up down restart clean test lint typecheck quality backup restore

PYTHON ?= python3

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## First-run setup — prompts for API keys, generates secrets, creates .env
	@bash ./scripts/setup-perseus.sh

start: ## Start Perseus workers + official Hermes + dashboard/frontend
	@if [ ! -f .env ]; then echo "No .env found — running first-time setup..."; bash ./scripts/setup-perseus.sh; fi
	@./scripts/start-perseus.sh

stop: ## Stop Perseus workers + official Hermes + dashboard/frontend
	@./scripts/stop-perseus.sh

up: ## Start Docker services only (Postgres, Qdrant, Mem0, N8N)
	docker compose up -d
	@echo "Waiting for services..."
	@sleep 10
	@docker compose ps
	@echo ""
	@echo "Starting Ollama (if not running)..."
	@pgrep -x ollama > /dev/null || (ollama serve &)
	@sleep 2
	@curl -sf http://localhost:11434/api/tags > /dev/null && echo "Ollama running" || echo "Ollama not responding"

down: ## Stop Docker services
	docker compose down

restart: ## Restart all daemons
	@./scripts/stop-perseus.sh
	@sleep 2
	@./scripts/start-perseus.sh

status: ## System status
	@echo "======================================"
	@echo "  PERSEUS — System Status"
	@echo "======================================"
	@echo ""
	@echo "=== Local Processes ==="
	@for agent in orchestrator titan clawdbot hermes dashboard frontend; do \
		if [ -f logs/pids/$$agent.pid ] && kill -0 $$(cat logs/pids/$$agent.pid) 2>/dev/null; then \
			echo "  $$agent: RUNNING (PID: $$(cat logs/pids/$$agent.pid))"; \
		else \
			echo "  $$agent: STOPPED"; \
		fi; \
	done
	@echo ""
	@echo "=== Hermes Gateway ==="
	@hermes gateway status 2>/dev/null | sed 's/^/  /' || echo "  Hermes gateway unavailable"
	@echo ""
	@echo "=== Docker ==="
	@docker compose ps 2>/dev/null || echo "Docker not running"
	@echo ""
	@echo "=== Ollama ==="
	@curl -sf http://localhost:11434/api/tags 2>/dev/null | python3 -c "import sys,json; [print(f'  {m[\"name\"]}') for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null || echo "  Not responding"
	@echo ""
	@echo "=== Disk ==="
	@df -h / | tail -1

logs: ## Tail daemon logs
	@tail -f logs/perseus.log logs/titan.log logs/clawdbot.log logs/dashboard.log $(HOME)/.hermes/logs/gateway.log 2>/dev/null || echo "No log files yet"

health: ## Quick health check
	@echo "Postgres:  $$(docker exec perseus-postgres pg_isready 2>/dev/null && echo 'OK' || echo 'DOWN')"
	@echo "Qdrant:    $$(curl -sf http://localhost:6333/collections > /dev/null && echo 'OK' || echo 'DOWN')"
	@echo "Mem0:      $$(curl -sf http://localhost:8888/health > /dev/null && echo 'OK' || echo 'DOWN')"
	@echo "N8N:       $$(curl -sf http://localhost:5678/healthz > /dev/null && echo 'OK' || echo 'DOWN')"
	@echo "Ollama:    $$(curl -sf http://localhost:11434/api/tags > /dev/null && echo 'OK' || echo 'DOWN')"

dashboard: ## Start dashboard backend (standalone, port 8500)
	$(PYTHON) -m uvicorn hermes.web.app:app --host 0.0.0.0 --port 8500 --reload

test: ## Run tests (no infrastructure needed)
	$(PYTHON) -m pytest tests/ -v

lint: ## Lint first-party code
	$(PYTHON) -m ruff check shared perseus titan hermes clawdbot tests

typecheck: ## Type-check first-party code
	$(PYTHON) -m mypy shared perseus titan hermes clawdbot

quality: lint typecheck test ## Run the local quality gates

backup: ## Create a Postgres backup in backups/
	@./scripts/backup-postgres.sh

restore: ## Restore Postgres from BACKUP=/abs/path/to/file.sql.gz
	@if [ -z "$(BACKUP)" ]; then \
		echo "Usage: make restore BACKUP=/absolute/path/to/backup.sql.gz"; \
		exit 1; \
	fi
	@./scripts/restore-from-backup.sh "$(BACKUP)"

clean: ## Remove all Docker volumes (DESTRUCTIVE)
	@echo "WARNING: This will delete ALL data (Postgres, Qdrant, Mem0)!"
	@read -rp "Type 'yes' to confirm: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		docker compose down -v; \
		echo "All volumes removed."; \
	else \
		echo "Cancelled."; \
	fi

# NOTE: explorer and watch-explorer targets removed — scripts/scan_architecture.py and
# scripts/watch_and_update.sh were never implemented. See docs/EXPLORER.md for context.
