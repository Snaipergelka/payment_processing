import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.rabbit.broker.setup import MAIN_ROUTING_KEY
from src.background_tasks.outbox.models import OutboxEvent
from src.payments.models import Payment
from src.payments.schemas import PaymentCreateRequest


async def get_payment_by_idempotency_key(
    session: AsyncSession, idempotency_key: str
) -> Payment | None:
    result = await session.execute(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def get_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    result = await session.execute(select(Payment).where(Payment.id == payment_id))
    return result.scalar_one_or_none()


async def create_payment(
    session: AsyncSession,
    idempotency_key: str,
    data: PaymentCreateRequest,
) -> tuple[Payment, bool]:
    existing = await get_payment_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return existing, False
    payment_id = uuid.uuid4()
    payment = Payment(
        id=payment_id,
        amount=data.amount,
        currency=data.currency,
        description=data.description,
        metadata_=data.metadata,
        idempotency_key=idempotency_key,
        webhook_url=str(data.webhook_url),
    )
    session.add(payment)

    outbox_event = OutboxEvent(
        aggregate_type="payment",
        aggregate_id=payment_id,
        event_type="payment.created",
        routing_key=MAIN_ROUTING_KEY,
        payload={"payment_id": str(payment_id), "attempt": 1},
    )
    session.add(outbox_event)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await get_payment_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing, False
        raise

    await session.refresh(payment)
    return payment, True
