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
        migrate bench-llm sync remote tunnel clean

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

bench-llm: ## D1 model-serving benchmark (see scripts/bench_llm.py --help)
	$(UV) run python scripts/bench_llm.py $(ARGS)

# --- remote workflow: code lives here, services and data live on $(SERVER) -----------------
sync: ## rsync the working tree to the server (excludes .env, data/, models/, sessions)
	rsync -az --delete --exclude-from=.rsyncignore ./ $(SERVER):$(REMOTE_DIR)/

remote: sync ## Run a make target on the server: make remote T=test
	ssh -t $(SERVER) 'cd $(REMOTE_DIR) && export PATH="$$HOME/.local/bin:$$PATH" && make $(T)'

tunnel: ## Forward Langfuse (3000), MinIO (9090), Qdrant (6333), LLM (8080) to localhost
	ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 6333:127.0.0.1:6333 -L 8080:127.0.0.1:8080 $(SERVER)

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
