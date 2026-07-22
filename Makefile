# Makefile for the Zillow scraper API.
#
# Everything normally runs through Docker Compose (redis + web + celery + celery-beat);
# the web app listens on port 8112. Targets prefixed `dev-` run against a local
# virtualenv instead and need a reachable Redis.
#
# Override any variable on the command line, e.g.:
#   make logs S=celery
#   make test ARGS=api.tests.HealthCheckTests
#   make up COMPOSE="docker-compose"

COMPOSE ?= docker compose
SERVICE ?= web
PORT    ?= 8112
HOST    ?= http://localhost:$(PORT)
PYTHON  ?= python
ARGS    ?=
S       ?= $(SERVICE)

# Run a one-off command inside the web container.
DEXEC = $(COMPOSE) exec $(SERVICE)

.DEFAULT_GOAL := help

.PHONY: help build up up-fg down down-v restart ps logs shell django-shell \
        migrate makemigrations superuser collectstatic test check health \
        schema install dev dev-migrate dev-test clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

## --- Docker Compose ---------------------------------------------------------

build: ## Build (or rebuild) the images
	$(COMPOSE) build

up: ## Start all services in the background
	$(COMPOSE) up -d --build

up-fg: ## Start all services in the foreground (Ctrl-C to stop)
	$(COMPOSE) up --build

down: ## Stop all services
	$(COMPOSE) down

down-v: ## Stop all services and delete volumes (wipes the Redis cache)
	$(COMPOSE) down -v

restart: ## Restart a single service (default: web) — make restart S=celery
	$(COMPOSE) restart $(S)

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Tail logs for one service (default: web) — make logs S=celery
	$(COMPOSE) logs -f $(S)

shell: ## Open a shell in the web container
	$(DEXEC) bash

django-shell: ## Open a Django shell in the web container
	$(DEXEC) $(PYTHON) manage.py shell

## --- Django -----------------------------------------------------------------

migrate: ## Apply database migrations
	$(DEXEC) $(PYTHON) manage.py migrate

makemigrations: ## Generate new migrations
	$(DEXEC) $(PYTHON) manage.py makemigrations $(ARGS)

superuser: ## Create an admin user (interactive)
	$(COMPOSE) exec $(SERVICE) $(PYTHON) manage.py createsuperuser

collectstatic: ## Collect static files
	$(DEXEC) $(PYTHON) manage.py collectstatic --noinput

test: ## Run tests — make test ARGS=api.tests.ClassName.test_method
	$(DEXEC) $(PYTHON) manage.py test $(ARGS)

check: ## Run Django's system checks (deployment settings included)
	$(DEXEC) $(PYTHON) manage.py check --deploy

schema: ## Write the OpenAPI schema to zillow-openapi-schema.yaml
	$(DEXEC) $(PYTHON) manage.py spectacular --file zillow-openapi-schema.yaml

## --- Utilities --------------------------------------------------------------

health: ## Hit /health and fail if the service is not "ok"
	@out=$$(curl -s $(HOST)/health); echo "$$out"; \
		echo "$$out" | grep -q '"status":"ok"' || { echo "UNHEALTHY"; exit 1; }

clean: ## Remove __pycache__ dirs and .pyc files
	find . -path ./.venv -prune -o -type d -name __pycache__ -print0 | xargs -0 rm -rf
	find . -path ./.venv -prune -o -type f -name '*.pyc' -print0 | xargs -0 rm -f

## --- Local (non-Docker) dev -------------------------------------------------

install: ## Install Python dependencies into the current environment
	$(PYTHON) -m pip install -r requirements.txt

dev: ## Run the Django dev server locally on PORT (default 8112)
	$(PYTHON) manage.py runserver $(PORT)

dev-migrate: ## Apply migrations locally
	$(PYTHON) manage.py migrate

dev-test: ## Run tests locally — make dev-test ARGS=api.tests
	$(PYTHON) manage.py test $(ARGS)
