# Payment Processing Service

Асинхронный микросервис обработки платежей: принимает запрос на оплату,
кладёт событие в очередь через транзакционный Outbox, обрабатывает его одним
consumer'ом (эмуляция шлюза + запись статуса + webhook), подтверждённо
публикует outbox-события в RabbitMQ и использует retry/DLQ при сбоях.

## Запуск

### Запуск сервиса
```bash
make up
```

### Запуск тестов
```bash
make install
make test
```

```bash
make down            # остановка сервиса
make logs            # логи всех сервисов
make api-logs        # логи только api
make consumer-logs   # логи только consumer
make migrate         # прогнать миграции ещё раз вручную
make down            # остановить всё (данные в volume сохраняются)
make clean           # остановить и снести volume (удалит БД)
```

## Примеры запросов

Ниже `$API_KEY` - значение из вашего `.env` (`export API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2)`), `$PAYMENT_ID` - айди платежа из ответа на запрос к АПИ POST http://localhost:8000/api/v1/payments 

Создание платежа (обязателен `Idempotency-Key`):

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: 3f29b0b1-1e2e-4a8a-9c3a-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
        "amount": 199.90,
        "currency": "RUB",
        "description": "Order #1001",
        "metadata": {"order_id": 1001, "user_id": 42},
        "webhook_url": "http://host.docker.internal:9000/webhook"
      }'
```

```bash
curl http://localhost:8000/api/v1/payments/$PAYMENT_ID \
  -H "X-API-Key: $API_KEY"
```

## Стек

- FastAPI + Pydantic v2
- SQLAlchemy 2.0 (async, asyncpg)
- PostgreSQL 16
- RabbitMQ 3.13 (FastStream)
- Alembic
- Docker / docker-compose
- Poetry (управление зависимостями), Make (обёртка над docker compose)

## Структура проекта

```
app/
  main.py            FastAPI app, lifespan запускает outbox relay
  config.py          настройки (pydantic-settings)
  db.py              async engine/session, Base
  models.py          Payment, OutboxEvent (SQLAlchemy 2.0)
  schemas.py         Pydantic v2 схемы запросов/ответов
  security.py        зависимость X-API-Key
  broker.py          топология RabbitMQ (exchanges/queues), общий для api и consumer
  events.py          Pydantic-схема сообщения payments.new
  api/payments.py    роутер: POST/GET /api/v1/payments
  services/payments.py  идемпотентное создание платежа + запись outbox (1 транзакция)
  outbox/relay.py    background-задача, публикующая outbox в RabbitMQ
consumer/
  app.py             единственный consumer: эмуляция шлюза + webhook + retry/DLQ
migrations/           Alembic (async env.py + начальная миграция)
tests/
  conftest.py             фикстуры: engine/сессия на реальный тестовый Postgres + httpx test-клиент FastAPI
  test_idempotency.py     идемпотентность: дубли, гонка на commit, unique-constraint
  test_api.py             401/404/422 эндпоинтов, идемпотентный POST, маппинг полей в GET
  test_outbox_relay.py    outbox: публикация/ретрай, стейл-реконсиляция и её лимит
  test_consumer.py        consumer: retry/DLQ роутинг, бизнес-отказ vs техническая ошибка
  mock_webhook_server.py  тестовый HTTP-приёмник вебхуков (для ручной проверки)
docker-compose.yml
docker-compose.test.yml  одноразовый Postgres для `make test` (см. раздел "Тесты")
Dockerfile
Makefile
pyproject.toml         зависимости (Poetry)
```

## Ограничения и что можно улучшить дальше

- Outbox relay реализован как background-таск, если сервис будет масштабироваться, его нужно будет вынести в отдельный сервис.
- Ретраи считаются по счётчику `attempt` в теле сообщения, а не по
  `x-death`/заголовкам RabbitMQ.
- Добавить систему алертов в DLQ-хендлер (сейчас он только логирует сообщение).
- Нет rate limiting и постраничного списка платежей (`GET /payments`).
