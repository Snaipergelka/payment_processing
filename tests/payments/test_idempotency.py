from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.background_tasks.outbox.models import OutboxEvent, OutboxStatus
from src.payments import service as payments_service
from src.payments.models import Payment
from src.payments.schemas import PaymentCreateRequest
from src.payments.service import create_payment, get_payment_by_idempotency_key
from tests.conftest import new_idempotency_key, valid_payment_payload


def _make_request(**overrides) -> PaymentCreateRequest:
    return PaymentCreateRequest(**valid_payment_payload(**overrides))


async def test_create_payment_writes_payment_and_outbox_event_atomically(db_session):
    key = new_idempotency_key()

    payment, created = await create_payment(db_session, key, _make_request())

    assert created is True
    assert payment.idempotency_key == key

    result = await db_session.execute(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == payment.id)
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "payment.created"
    assert events[0].status == OutboxStatus.PENDING
    assert events[0].payload == {"payment_id": str(payment.id), "attempt": 1}


async def test_duplicate_idempotency_key_returns_existing_payment(db_session):
    key = new_idempotency_key()

    first, first_created = await create_payment(db_session, key, _make_request())
    second, second_created = await create_payment(db_session, key, _make_request(amount="999.00"))

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    # the second (different) request payload must NOT overwrite the original row
    assert second.amount == Decimal("100.50")

    result = await db_session.execute(select(Payment).where(Payment.idempotency_key == key))
    assert len(result.scalars().all()) == 1


async def test_idempotency_key_is_unique_at_the_db_level(db_session):
    """Belt-and-suspenders: even if the service-layer pre-check were ever
    buggy/skipped, the DB itself must refuse a second row with the same key."""
    key = new_idempotency_key()
    db_session.add(
        Payment(
            amount=Decimal("1.00"),
            currency="RUB",
            idempotency_key=key,
            webhook_url="https://example.com/webhook",
        )
    )
    await db_session.commit()

    db_session.add(
        Payment(
            amount=Decimal("2.00"),
            currency="RUB",
            idempotency_key=key,
            webhook_url="https://example.com/webhook",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_concurrent_create_payment_race_falls_back_to_existing_row(db_session, monkeypatch):
    """Simulates two requests racing on the same Idempotency-Key: both pass
    the "does it exist?" check before either commits. `create_payment` must
    not let two rows through - the loser has to detect the resulting
    IntegrityError and transparently return the winner's row instead of
    raising.
    """
    key = new_idempotency_key()

    # A "winner" that committed after our stale read below already happened.
    winner = Payment(
        amount=Decimal("1.00"),
        currency="RUB",
        idempotency_key=key,
        webhook_url="https://example.com/webhook",
    )
    db_session.add(winner)
    await db_session.commit()

    original_lookup = get_payment_by_idempotency_key
    call_count = {"n": 0}

    async def stale_read_once(session, idempotency_key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # pretend the row isn't visible yet (the race)
        return await original_lookup(session, idempotency_key)

    monkeypatch.setattr(payments_service, "get_payment_by_idempotency_key", stale_read_once)

    payment, created = await create_payment(db_session, key, _make_request())

    assert created is False
    assert payment.id == winner.id


async def test_different_idempotency_keys_create_different_payments(db_session):
    payment_a, _ = await create_payment(db_session, new_idempotency_key(), _make_request())
    payment_b, _ = await create_payment(db_session, new_idempotency_key(), _make_request())

    assert payment_a.id != payment_b.id
