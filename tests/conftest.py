import os

TEST_API_KEY = "test-api-key"
os.environ.setdefault("API_KEY", TEST_API_KEY)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://payments_test:payments_test@localhost:55432/payments_test",
)

import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.database import Base
from src.background_tasks.outbox import models as outbox_models  # noqa: F401  register OutboxEvent
from src.payments import models as payments_models  # noqa: F401  register Payment on Base.metadata

_TABLES = ["outbox_events", "payments"]


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    test_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield test_engine
    finally:
        await test_engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def db_session(session_factory: async_sessionmaker) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def api_client(session_factory: async_sessionmaker) -> AsyncIterator[AsyncClient]:
    from src.database import get_session
    from src.main import app as fastapi_app

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    fastapi_app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict:
    return {"X-API-Key": TEST_API_KEY}


def valid_payment_payload(**overrides) -> dict:
    payload = {
        "amount": "100.50",
        "currency": "RUB",
        "description": "Test payment",
        "metadata": {"order_id": 1},
        "webhook_url": "https://example.com/webhook",
    }
    payload.update(overrides)
    return payload


def new_idempotency_key() -> str:
    return str(uuid.uuid4())
