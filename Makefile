COMPOSE ?= docker compose
VENV    ?= .venv
PY      := $(VENV)/bin/python

.PHONY: help venv test lint up down reset logs ps check migrate

help: ## показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-8s %s\n", $$1, $$2}'

venv: ## создать окружение и поставить пакеты для разработки
	python3.12 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "libs/common[dev]" -e services/ticket

test: ## запустить тесты
	$(PY) -m pytest -q libs/common/tests services/ticket/tests

lint: ## проверить стиль кода
	$(PY) -m ruff check .

up: ## собрать и поднять стенд
	$(COMPOSE) up -d --build

down: ## остановить стенд (данные сохраняются)
	$(COMPOSE) down

reset: ## остановить стенд и удалить ВСЕ данные (тома)
	$(COMPOSE) down -v

logs: ## логи: make logs s=ticket-service
	$(COMPOSE) logs -f --tail=100 $(s)

ps: ## состояние контейнеров
	$(COMPOSE) ps

migrate: ## применить миграции tickets_db
	$(COMPOSE) run --rm --build ticket-migrate

check: ## быстрая проверка стенда (этап 1)
	@curl -fsS http://localhost/hello && echo
	@$(COMPOSE) exec -T postgres psql -U postgres -tAc "SELECT datname FROM pg_database WHERE datname LIKE '%_db'"
	@$(COMPOSE) exec -T redis redis-cli ping
	@$(COMPOSE) exec -T rabbitmq rabbitmq-diagnostics -q ping && echo "rabbitmq ok"
