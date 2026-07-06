import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

from sqlalchemy import select

import src.background_tasks.outbox.relay as relay
from src.background_tasks.outbox.models import OutboxEvent, OutboxStatus
from src.background_tasks.outbox.relay import _publish_pending_batch, _requeue_stale_pending_payments
from src.config import get_settings
from src.payments.models import Payment, PaymentStatus
from src.rabbit.broker.setup import MAIN_ROUTING_KEY

settings = get_settings()


def _old_enough() -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        seconds=settings.stale_pending_threshold_seconds + 30
    )


def _pending_payment(**overrides) -> Payment:
    defaults = dict(
        amount=Decimal("10.00"),
        currency="RUB",
        idempotency_key=str(uuid.uuid4()),
        webhook_url="https://example.com/webhook",
        status=PaymentStatus.PENDING,
    )
    defaults.update(overrides)
    return Payment(**defaults)


async def _add_outbox_event(session, payment: Payment) -> OutboxEvent:
    event = OutboxEvent(
        aggregate_type="payment",
        aggregate_id=payment.id,
        event_type="payment.created",
        routing_key=MAIN_ROUTING_KEY,
        payload={"payment_id": str(payment.id), "attempt": 1},
    )
    session.add(event)
    await session.commit()
    return event


# --- Publishing pending outbox rows ------------------------------------------


async def test_publish_pending_batch_marks_event_published_on_success(db_session, monkeypatch):
    payment = _pending_payment()
    db_session.add(payment)
    await db_session.flush()
    event = await _add_outbox_event(db_session, payment)

    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(relay, "_publish_outbox_event", publish_mock)

    published_count = await _publish_pending_batch(db_session)

    assert published_count == 1
    publish_mock.assert_awaited_once()
    await db_session.refresh(event)
    assert event.status == OutboxStatus.PUBLISHED
    assert event.published_at is not None


async def test_publish_pending_batch_keeps_event_pending_on_broker_failure(db_session, monkeypatch):
    payment = _pending_payment()
    db_session.add(payment)
    await db_session.flush()
    event = await _add_outbox_event(db_session, payment)

    monkeypatch.setattr(
        relay,
        "_publish_outbox_event",
        AsyncMock(side_effect=RuntimeError("broker down")),
    )

    published_count = await _publish_pending_batch(db_session)

    assert published_count == 0
    await db_session.refresh(event)
    assert event.status == OutboxStatus.PENDING
    assert event.attempts == 1
    assert "broker down" in event.last_error
    assert event.published_at is None


async def test_publish_pending_batch_is_noop_when_nothing_pending(db_session, monkeypatch):
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(relay, "_publish_outbox_event", publish_mock)

    published_count = await _publish_pending_batch(db_session)

    assert published_count == 0
    publish_mock.assert_not_awaited()


async def test_publish_pending_batch_does_not_touch_already_published_events(
    db_session, monkeypatch
):
    payment = _pending_payment()
    db_session.add(payment)
    await db_session.flush()
    event = await _add_outbox_event(db_session, payment)
    event.status = OutboxStatus.PUBLISHED
    event.published_at = datetime.now(timezone.utc)
    await db_session.commit()

    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(relay, "_publish_outbox_event", publish_mock)

    published_count = await _publish_pending_batch(db_session)

    assert published_count == 0
    publish_mock.assert_not_awaited()


# --- Stale-payment reconciliation --------------------------------------------


async def test_requeue_stale_pending_payment_publishes_new_event(db_session):
    payment = _pending_payment(created_at=_old_enough())
    db_session.add(payment)
    await db_session.commit()

    requeued = await _requeue_stale_pending_payments(db_session)

    assert requeued == 1
    await db_session.refresh(payment)
    assert payment.requeue_count == 1
    assert payment.last_requeued_at is not None

    result = await db_session.execute(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == payment.id)
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "payment.requeued"
    assert events[0].payload == {"payment_id": str(payment.id), "attempt": 1}
    assert events[0].status == OutboxStatus.PENDING


async def test_requeue_ignores_recent_pending_payment(db_session):
    payment = _pending_payment()  # created_at defaults to "now" via server_default
    db_session.add(payment)
    await db_session.commit()

    requeued = await _requeue_stale_pending_payments(db_session)

    assert requeued == 0


async def test_requeue_ignores_non_pending_payment_even_if_old(db_session):
    payment = _pending_payment(created_at=_old_enough(), status=PaymentStatus.SUCCEEDED)
    db_session.add(payment)
    await db_session.commit()

    requeued = await _requeue_stale_pending_payments(db_session)

    assert requeued == 0


async def test_requeue_respects_cooldown_after_a_recent_requeue(db_session):
    """A payment requeued a moment ago should not be requeued again on the
    very next sweep, even though it's still (old and) pending."""
    payment = _pending_payment(
        created_at=_old_enough(),
        last_requeued_at=datetime.now(timezone.utc),
    )
    db_session.add(payment)
    await db_session.commit()

    requeued = await _requeue_stale_pending_payments(db_session)

    assert requeued == 0


async def test_requeue_stops_after_max_attempts(db_session):
    payment = _pending_payment(
        created_at=_old_enough(),
        requeue_count=settings.max_stale_requeue_attempts - 1,
        last_requeued_at=_old_enough(),
    )
    db_session.add(payment)
    await db_session.commit()

    requeued_once = await _requeue_stale_pending_payments(db_session)
    assert requeued_once == 1
    await db_session.refresh(payment)
    assert payment.requeue_count == settings.max_stale_requeue_attempts

    # even after "more time passes", it must not be picked up again
    payment.last_requeued_at = _old_enough()
    await db_session.commit()

    requeued_again = await _requeue_stale_pending_payments(db_session)
    assert requeued_again == 0


async def test_requeue_handles_multiple_stale_payments_in_one_sweep(db_session):
    payment_a = _pending_payment(created_at=_old_enough())
    payment_b = _pending_payment(created_at=_old_enough())
    db_session.add_all([payment_a, payment_b])
    await db_session.commit()

    requeued = await _requeue_stale_pending_payments(db_session)

    assert requeued == 2
