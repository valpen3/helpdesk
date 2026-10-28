COMPOSE ?= docker compose
VENV    ?= .venv
PY      := $(VENV)/bin/python

.PHONY: help venv test lint up down logs ps

help: ## показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-8s %s\n", $$1, $$2}'

venv: ## создать окружение и поставить libs/common
	python3.12 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "libs/common[dev]"

test: ## запустить тесты
	$(PY) -m pytest -q libs/common/tests

lint: ## проверить стиль кода
	$(PY) -m ruff check .

up: ## поднять стенд (с этапа 1)
	$(COMPOSE) up -d --build

down: ## остановить стенд
	$(COMPOSE) down

logs: ## логи: make logs s=ticket-service
	$(COMPOSE) logs -f --tail=100 $(s)

ps: ## состояние контейнеров
	$(COMPOSE) ps
