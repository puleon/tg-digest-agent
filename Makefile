.DEFAULT_GOAL := help
SHELL := /bin/bash
.ONESHELL:

# Remote box that runs the services and the models. Only an ssh alias lives here;
# host/port/user stay in ~/.ssh/config on the developer machine.
SERVER     ?= shevek
REMOTE_DIR ?= tg-digest-agent
COMPOSE    ?= docker compose
UV         ?= uv

.PHONY: help install up down restart ps logs health test test-int lint fmt typecheck check \
        migrate api bot bench-serving bench-llm sync pull-results remote collect collect-channels refresh tunnel egress clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --- local dev -------------------------------------------------------------------------------
install: ## Create the venv, install deps and git hooks
	$(UV) sync --all-groups
	$(UV) run pre-commit install

test: ## Unit tests (no services needed)
	$(UV) run pytest

test-int: ## Integration tests against running services
	$(UV) run pytest -m integration -o addopts=""

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-format and fix lint
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## mypy (strict)
	$(UV) run mypy

check: lint typecheck test ## Everything CI runs

# --- services (run on the server, or locally if you have the RAM) ---------------------------
up: ## Start all services and wait until healthy
	test -f .env || { echo ".env missing — copy .env.example and fill it in"; exit 1; }
	$(COMPOSE) up -d --wait
	$(UV) run tgdigest health

down: ## Stop services (volumes are kept)
	$(COMPOSE) down

restart: ## Restart one service: make restart S=llm
	$(COMPOSE) restart $(S)

ps: ## Service status
	$(COMPOSE) ps

logs: ## Tail logs: make logs S=llm
	$(COMPOSE) logs -f --tail=200 $(S)

health: ## Reachability of Postgres / Qdrant / LLM / Langfuse
	$(UV) run tgdigest health

migrate: ## Apply Alembic migrations
	$(UV) run alembic upgrade head

api: ## Serve the HTTP API on API_HOST:API_PORT (models load at start; ~1 min on CPU)
	$(UV) run tgdigest api

bot: ## Run the Telegram bot against API_URL (needs BOT_TOKEN; BOT_PROXY when Telegram is unreachable)
	$(UV) run tgdigest bot

bench-serving: ## D1 serving benchmark matrix on the box (writes docs/experiments/d1-llm-benchmark/results/)
	scripts/bench_serving.sh --image data/samples/meme.png

bench-llm: ## Single-target benchmark (see scripts/bench_llm.py --help)
	$(UV) run python scripts/bench_llm.py $(ARGS)

# --- remote workflow: code lives here, services and data live on $(SERVER) -----------------
sync: ## rsync the working tree to the server (excludes .env, data/, models/, sessions)
	rsync -az --delete --exclude-from=.rsyncignore ./ $(SERVER):$(REMOTE_DIR)/

pull-results: ## Copy experiment outputs (docs/experiments/*/results/) back from the server
	rsync -az --include='*/' --include='results/***' --exclude='*' $(SERVER):$(REMOTE_DIR)/docs/experiments/ docs/experiments/

remote: sync ## Run a make target on the server: make remote T=test
	ssh -t $(SERVER) 'cd $(REMOTE_DIR) && export PATH="$$HOME/.local/bin:$$PATH" && make $(T)'

# The box cannot reach t.me; these run the collector there with its egress routed back through
# this machine (OpenSSH reverse SOCKS: -R 1080 with no target). Needs WEB_PROXY in the box .env.
collect-channels: sync ## Resolve config/channels.yaml on the box via the reverse tunnel
	ssh -o ExitOnForwardFailure=yes -R 1080 $(SERVER) 'cd $(REMOTE_DIR) && export PATH="$$HOME/.local/bin:$$PATH" && uv run tgdigest collector channels'

collect: sync ## Incremental sync on the box via the reverse tunnel: make collect ARGS="--topic humor"
	ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 1080 $(SERVER) 'cd $(REMOTE_DIR) && export PATH="$$HOME/.local/bin:$$PATH" && uv run tgdigest collector sync $(ARGS)'

refresh: sync ## Re-read dates and view counters of stored posts via the reverse tunnel (no media)
	ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 1080 $(SERVER) 'cd $(REMOTE_DIR) && export PATH="$$HOME/.local/bin:$$PATH" && uv run tgdigest collector refresh $(ARGS)'

tunnel: ## Forward Langfuse (3000), MinIO (9090), Qdrant (6333), LLM (8080), API (8000) to localhost
	ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 6333:127.0.0.1:6333 -L 8080:127.0.0.1:8080 -L 8000:127.0.0.1:8000 $(SERVER)

egress: ## Reverse SOCKS on the box (:1080) so the bot there can reach Telegram through this machine (BOT_PROXY)
	ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 1080 $(SERVER)

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
