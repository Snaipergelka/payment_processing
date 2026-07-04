SHELL := /bin/bash
COMPOSE := docker compose
ENV_FILE := .env
ENV_EXAMPLE := .env.example

TEST_COMPOSE := docker compose -f docker-compose.test.yml
TEST_DATABASE_URL := postgresql+asyncpg://payments_test:payments_test@localhost:55432/payments_test

.PHONY: help env up down build restart logs api-logs consumer-logs ps migrate rabbitmq-init reset-rabbitmq clean install test test-db-up test-db-down lint

help:
	@echo "make env             Generate .env with random secrets (skipped if it already exists)"
	@echo "make up              Generate .env if needed, build images, start every service"
	@echo "make down            Stop all services (keeps data volumes)"
	@echo "make build           Rebuild images without starting"
	@echo "make restart         down + up"
	@echo "make logs            Tail logs for all services"
	@echo "make api-logs        Tail API logs only"
	@echo "make consumer-logs   Tail consumer logs only"
	@echo "make ps              Show service status"
	@echo "make migrate         Re-run Alembic migrations on demand"
	@echo "make rabbitmq-init   Re-declare RabbitMQ topology on demand"
	@echo "make reset-rabbitmq  Wipe ONLY the RabbitMQ volume (keeps Postgres data)"
	@echo "make clean           Stop services and delete volumes (drops the DB!)"
	@echo "make install         poetry install (local dev, outside Docker)"
	@echo "make test            Spin up a throwaway test Postgres, run pytest, tear it down"
	@echo "make test-db-up      Start the test Postgres only (for a faster local test loop)"
	@echo "make test-db-down    Stop and remove the test Postgres"
	@echo "make lint            Run ruff via Poetry"

env:
	@if [ -f $(ENV_FILE) ]; then \
		echo "$(ENV_FILE) already exists, leaving it untouched."; \
	else \
		echo "Generating $(ENV_FILE) from $(ENV_EXAMPLE) with fresh random secrets..."; \
		cp $(ENV_EXAMPLE) $(ENV_FILE); \
		API_KEY=$$(python3 -c "import secrets; print(secrets.token_urlsafe(32))"); \
		PG_PASSWORD=$$(python3 -c "import secrets; print(secrets.token_urlsafe(16))"); \
		RMQ_PASSWORD=$$(python3 -c "import secrets; print(secrets.token_urlsafe(16))"); \
		sed -i.bak "s#^API_KEY=.*#API_KEY=$$API_KEY#" $(ENV_FILE); \
		sed -i.bak "s#^POSTGRES_PASSWORD=.*#POSTGRES_PASSWORD=$$PG_PASSWORD#" $(ENV_FILE); \
		sed -i.bak "s#^RABBITMQ_PASSWORD=.*#RABBITMQ_PASSWORD=$$RMQ_PASSWORD#" $(ENV_FILE); \
		sed -i.bak "s#^DATABASE_URL=.*#DATABASE_URL=postgresql+asyncpg://payments:$$PG_PASSWORD@postgres:5432/payments#" $(ENV_FILE); \
		sed -i.bak "s#^RABBITMQ_URL=.*#RABBITMQ_URL=amqp://guest:$$RMQ_PASSWORD@rabbitmq:5672/#" $(ENV_FILE); \
		rm -f $(ENV_FILE).bak; \
		echo "$(ENV_FILE) ready (API key + DB/broker passwords generated)."; \
	fi

up: env
	$(COMPOSE) up --build -d
	@echo ""
	@echo "API docs:     http://localhost:8000/docs"
	@echo "RabbitMQ UI:  http://localhost:15672"
	@echo "API key:      $$(grep '^API_KEY=' $(ENV_FILE) | cut -d= -f2)"

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build --no-cache

restart: down up

logs:
	$(COMPOSE) logs -f

api-logs:
	$(COMPOSE) logs -f api

consumer-logs:
	$(COMPOSE) logs -f consumer

ps:
	$(COMPOSE) ps

migrate: env
	$(COMPOSE) run --rm migrate

rabbitmq-init: env
	$(COMPOSE) run --rm rabbitmq-init

reset-rabbitmq: down
	docker volume rm $$(docker volume ls -q --filter name=rabbitmq_data) 2>/dev/null || true
	$(MAKE) up

clean:
	$(COMPOSE) down -v

install:
	poetry install

test-db-up:
	$(TEST_COMPOSE) up -d
	@echo "Waiting for test Postgres to accept connections..."
	@until $(TEST_COMPOSE) exec -T postgres-test pg_isready -U payments_test -d payments_test >/dev/null 2>&1; do sleep 1; done
	@echo "Test Postgres ready on localhost:55432"

test-db-down:
	$(TEST_COMPOSE) down -v

test: test-db-up
	@TEST_DATABASE_URL=$(TEST_DATABASE_URL) poetry run pytest; \
	status=$$?; \
	$(MAKE) test-db-down; \
	exit $$status

lint:
	poetry run ruff check .
