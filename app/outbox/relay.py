import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker import MAIN_ROUTING_KEY, broker, main_exchange
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import OutboxEvent, OutboxStatus, Payment, PaymentStatus

logger = logging.getLogger(__name__)
settings = get_settings()


def _skip_locked(stmt: Select, session: AsyncSession) -> Select:
    if session.get_bind().dialect.name == "postgresql":
        return stmt.with_for_update(skip_locked=True)
    return stmt


async def _publish_pending_batch(session: AsyncSession) -> int:
    result = await session.execute(
        _skip_locked(
            select(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING)
            .order_by(OutboxEvent.created_at)
            .limit(settings.outbox_batch_size),
            session,
        )
    )
    events = list(result.scalars().all())
    if not events:
        return 0

    published = 0
    for event in events:
        try:
            await broker.publish(
                event.payload,
                exchange=main_exchange,
                routing_key=event.routing_key,
            )
        except Exception as exc:  # noqa: BLE001 - broker/network errors, retried next poll
            event.attempts += 1
            event.last_error = str(exc)[:2000]
            logger.warning("Failed to publish outbox event %s: %s", event.id, exc)
        else:
            event.status = OutboxStatus.PUBLISHED
            event.published_at = datetime.now(timezone.utc)
            published += 1

    await session.commit()
    return published


async def _requeue_stale_pending_payments(session: AsyncSession) -> int:
    threshold = datetime.now(timezone.utc) - timedelta(
        seconds=settings.stale_pending_threshold_seconds
    )
    result = await session.execute(
        _skip_locked(
            select(Payment)
            .where(
                Payment.status == PaymentStatus.PENDING,
                Payment.created_at < threshold,
                Payment.requeue_count < settings.max_stale_requeue_attempts,
                or_(Payment.last_requeued_at.is_(None), Payment.last_requeued_at < threshold),
            )
            .order_by(Payment.created_at)
            .limit(settings.outbox_batch_size),
            session,
        )
    )
    payments = list(result.scalars().all())
    if not payments:
        return 0

    now = datetime.now(timezone.utc)
    for payment in payments:
        payment.requeue_count += 1
        payment.last_requeued_at = now
        if payment.requeue_count >= settings.max_stale_requeue_attempts:
            logger.error(
                "Payment %s stuck in pending since %s - requeuing for the last time "
                "(%d/%d attempts used). If it's still pending after this, it needs "
                "manual investigation - automatic recovery will stop retrying it.",
                payment.id,
                payment.created_at,
                payment.requeue_count,
                settings.max_stale_requeue_attempts,
            )
        else:
            logger.warning(
                "Payment %s stuck in pending since %s - re-publishing payments.new "
                "(attempt %d/%d)",
                payment.id,
                payment.created_at,
                payment.requeue_count,
                settings.max_stale_requeue_attempts,
            )
        session.add(
            OutboxEvent(
                aggregate_type="payment",
                aggregate_id=payment.id,
                event_type="payment.requeued",
                routing_key=MAIN_ROUTING_KEY,
                payload={"payment_id": str(payment.id), "attempt": 1},
            )
        )

    await session.commit()
    return len(payments)


async def outbox_relay_loop() -> None:
    logger.info(
        "Outbox relay started (poll interval=%.1fs, stale threshold=%.0fs)",
        settings.outbox_poll_interval_seconds,
        settings.stale_pending_threshold_seconds,
    )
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await _requeue_stale_pending_payments(session)
            async with AsyncSessionLocal() as session:
                await _publish_pending_batch(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outbox relay iteration failed")
        await asyncio.sleep(settings.outbox_poll_interval_seconds)
