import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from app.consumer import app as consumer_app
from app.events import PaymentNewEvent
from app.models import Payment, PaymentStatus


def _make_fake_async_client(behavior: str):
    calls: list[dict] = []

    class _FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, json=None):
            calls.append({"url": url, "json": json})
            if behavior == "success":
                return _FakeResponse(200)
            if behavior == "server_error":
                return _FakeResponse(500)
            if behavior == "raises":
                raise httpx.HTTPError("connection refused")
            raise AssertionError(f"unknown behavior: {behavior}")

    _FakeAsyncClient.calls = calls
    return _FakeAsyncClient


@pytest.fixture
def consumer_env(monkeypatch, session_factory):
    monkeypatch.setattr(consumer_app, "AsyncSessionLocal", session_factory)
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(consumer_app.broker, "publish", publish_mock)
    monkeypatch.setattr(consumer_app.asyncio, "sleep", AsyncMock(return_value=None))
    return publish_mock


async def _create_payment(session_factory, **overrides) -> Payment:
    defaults = dict(
        amount=Decimal("42.00"),
        currency="RUB",
        idempotency_key=str(uuid.uuid4()),
        webhook_url="https://example.com/webhook",
        status=PaymentStatus.PENDING,
    )
    defaults.update(overrides)
    async with session_factory() as session:
        payment = Payment(**defaults)
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment


FORCE_SUCCESS = 0.99
FORCE_FAILURE = 0.0


async def test_successful_processing_marks_succeeded_and_sends_webhook(
    consumer_env, session_factory, monkeypatch
):
    client_cls = _make_fake_async_client("success")
    monkeypatch.setattr(consumer_app.httpx, "AsyncClient", client_cls)
    monkeypatch.setattr(consumer_app.random, "random", lambda: FORCE_SUCCESS)

    payment = await _create_payment(session_factory)
    await consumer_app.handle_payment_new(PaymentNewEvent(payment_id=payment.id, attempt=1))

    async with session_factory() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.status == PaymentStatus.SUCCEEDED
        assert refreshed.webhook_delivered_at is not None
        assert refreshed.processed_at is not None

    assert len(client_cls.calls) == 1
    assert client_cls.calls[0]["json"]["status"] == "succeeded"
    consumer_env.assert_not_awaited()  # no retry/DLQ needed


async def test_business_failure_still_updates_status_and_sends_webhook(
    consumer_env, session_factory, monkeypatch
):
    monkeypatch.setattr(consumer_app.random, "random", lambda: FORCE_FAILURE)

    payment = await _create_payment(session_factory)
    await consumer_app.handle_payment_new(PaymentNewEvent(payment_id=payment.id, attempt=1))

    async with session_factory() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.status == PaymentStatus.PENDING
        assert refreshed.webhook_delivered_at is None
        assert refreshed.processed_at is None

    consumer_env.assert_awaited_once()
    args, kwargs = consumer_env.call_args
    assert kwargs["routing_key"] == "payments.new.retry.1"
    assert args[0]["attempt"] == 2


async def test_final_gateway_failure_marks_failed_and_sends_webhook(
    consumer_env, session_factory, monkeypatch
):
    client_cls = _make_fake_async_client("success")
    monkeypatch.setattr(consumer_app.httpx, "AsyncClient", client_cls)
    monkeypatch.setattr(consumer_app.random, "random", lambda: FORCE_FAILURE)

    payment = await _create_payment(session_factory)
    await consumer_app.handle_payment_new(
        PaymentNewEvent(payment_id=payment.id, attempt=consumer_app.settings.max_delivery_attempts)
    )

    async with session_factory() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.status == PaymentStatus.FAILED
        assert refreshed.webhook_delivered_at is not None
        assert refreshed.processed_at is not None

    assert client_cls.calls[0]["json"]["status"] == "failed"
    consumer_env.assert_not_awaited()


async def test_webhook_failure_schedules_retry_with_incremented_attempt(
    consumer_env, session_factory, monkeypatch
):
    monkeypatch.setattr(consumer_app.httpx, "AsyncClient", _make_fake_async_client("server_error"))
    monkeypatch.setattr(consumer_app.random, "random", lambda: FORCE_SUCCESS)

    payment = await _create_payment(session_factory)
    await consumer_app.handle_payment_new(PaymentNewEvent(payment_id=payment.id, attempt=1))

    async with session_factory() as session:
        refreshed = await session.get(Payment, payment.id)
        assert refreshed.status == PaymentStatus.SUCCEEDED
        assert refreshed.webhook_delivered_at is None

    consumer_env.assert_awaited_once()
    args, kwargs = consumer_env.call_args
    assert kwargs["routing_key"] == "payments.new.retry.1"
    assert args[0]["attempt"] == 2
    assert args[0]["payment_id"] == str(payment.id)


async def test_webhook_non_2xx_response_also_triggers_retry(
    consumer_env, session_factory, monkeypatch
):
    monkeypatch.setattr(consumer_app.httpx, "AsyncClient", _make_fake_async_client("server_error"))
    monkeypatch.setattr(consumer_app.random, "random", lambda: FORCE_SUCCESS)

    payment = await _create_payment(session_factory)
    await consumer_app.handle_payment_new(PaymentNewEvent(payment_id=payment.id, attempt=2))

    consumer_env.assert_awaited_once()
    args, kwargs = consumer_env.call_args
    assert kwargs["routing_key"] == "payments.new.retry.2"
    assert args[0]["attempt"] == 3


async def test_final_attempt_failure_goes_to_dlq_not_retry_queue(
    consumer_env, session_factory, monkeypatch
):
    monkeypatch.setattr(consumer_app.httpx, "AsyncClient", _make_fake_async_client("raises"))
    monkeypatch.setattr(consumer_app.random, "random", lambda: FORCE_SUCCESS)

    payment = await _create_payment(session_factory)
    max_attempts = consumer_app.settings.max_delivery_attempts

    await consumer_app.handle_payment_new(
        PaymentNewEvent(payment_id=payment.id, attempt=max_attempts)
    )

    consumer_env.assert_awaited_once()
    args, kwargs = consumer_env.call_args
    assert kwargs["routing_key"] == "payments.new.dlq"
    assert args[0]["payment_id"] == str(payment.id)
    assert "error" in args[0]
