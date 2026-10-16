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
        migrate api bot bench-serving bench-llm sync pull-results remote collect collect-channels refresh tunnel units clean

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

health: ## Reachability of Postgres / Qdrant / LLM / Langfuse / Telegram
	$(UV) run tgdigest health

migrate: ## Apply Alembic migrations
	$(UV) run alembic upgrade head

api: ## Serve the HTTP API on API_HOST:API_PORT (models load at start; ~1 min on CPU)
	$(UV) run tgdigest api

bot: ## Run the Telegram bot in the foreground against API_URL (needs BOT_TOKEN)
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

# The box reaches Telegram directly: a box-level VPN routes only Telegram's official prefixes
# (t.me, api.telegram.org, the telesco.pe CDN) into a tunnel and leaves everything else alone.
# Fallback if that route is down: `ssh -N -R 1080 $(SERVER)` from a machine that can reach
# Telegram, and WEB_PROXY / BOT_PROXY = socks5h://127.0.0.1:1080 in the box .env.
on_box = ssh -o ServerAliveInterval=30 $(SERVER) 'cd $(REMOTE_DIR) && export PATH="$$HOME/.local/bin:$$PATH" && $(1)'

collect-channels: sync ## Resolve config/channels.yaml on the box
	$(call on_box,uv run tgdigest collector channels)

collect: sync ## Incremental sync on the box: make collect ARGS="--topic humor"
	$(call on_box,uv run tgdigest collector sync $(ARGS))

refresh: sync ## Re-read dates and view counters of stored posts on the box (no media)
	$(call on_box,uv run tgdigest collector refresh $(ARGS))

tunnel: ## Forward Langfuse (3000), MinIO (9090), Qdrant (6333), LLM (8080), API (8000) to localhost
	ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 6333:127.0.0.1:6333 -L 8080:127.0.0.1:8080 -L 8000:127.0.0.1:8000 $(SERVER)

# The API and the bot run on the box as systemd user units (the box user has Linger=yes, so
# they start at boot without a login). `units` runs on the box: make remote T=units
units: ## Install the api/bot user units (deploy/systemd) into ~/.config/systemd/user
	install -d $$HOME/.config/systemd/user
	install -m 644 deploy/systemd/tgdigest-api.service deploy/systemd/tgdigest-bot.service $$HOME/.config/systemd/user/
	systemctl --user daemon-reload

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
