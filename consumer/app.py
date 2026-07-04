import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx

from faststream import FastStream

from app.broker import (
    DLQ_ROUTING_KEY,
    RETRY_QUEUES,
    RETRY_ROUTING_KEYS,
    broker,
    dlq_exchange,
    main_exchange,
    payments_dlq_queue,
    payments_new_queue,
    retry_exchange,
)
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.events import PaymentNewEvent
from app.models import Payment, PaymentStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastStream(broker)


class WebhookDeliveryError(Exception):
    pass


async def _run_gateway_emulation(payment: Payment) -> None:
    delay = random.uniform(settings.gateway_min_delay_seconds, settings.gateway_max_delay_seconds)
    await asyncio.sleep(delay)
    succeeded = random.random() >= settings.gateway_failure_rate
    payment.status = PaymentStatus.SUCCEEDED if succeeded else PaymentStatus.FAILED
    payment.processed_at = datetime.now(timezone.utc)
    logger.info("Payment %s gateway result: %s", payment.id, payment.status.value)


async def _send_webhook(payment: Payment) -> None:
    body = {
        "payment_id": str(payment.id),
        "status": payment.status.value,
        "amount": str(payment.amount),
        "currency": payment.currency.value,
        "description": payment.description,
        "metadata": payment.metadata_,
        "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
            response = await client.post(payment.webhook_url, json=body)
        if response.status_code >= 400:
            raise WebhookDeliveryError(f"webhook endpoint returned HTTP {response.status_code}")
    except httpx.HTTPError as exc:
        raise WebhookDeliveryError(f"webhook request failed: {exc}") from exc


async def _schedule_retry_or_dlq(event: PaymentNewEvent, error: Exception) -> None:
    attempt = event.attempt
    if attempt < settings.max_delivery_attempts:
        next_attempt = attempt + 1
        retry_queue = RETRY_QUEUES[attempt]
        routing_key = RETRY_ROUTING_KEYS[attempt]
        logger.warning(
            "Payment %s: attempt %d failed (%s). Scheduling attempt %d via %s.",
            event.payment_id,
            attempt,
            error,
            next_attempt,
            retry_queue.name,
        )
        retry_event = PaymentNewEvent(payment_id=event.payment_id, attempt=next_attempt)
        await broker.publish(
            retry_event.model_dump(mode="json"),
            exchange=retry_exchange,
            routing_key=routing_key,
        )
    else:
        logger.error(
            "Payment %s: exhausted %d attempts (last error: %s). Sending to DLQ.",
            event.payment_id,
            attempt,
            error,
        )
        await broker.publish(
            {**event.model_dump(mode="json"), "error": str(error)},
            exchange=dlq_exchange,
            routing_key=DLQ_ROUTING_KEY,
        )


@broker.subscriber(payments_new_queue, exchange=main_exchange)
async def handle_payment_new(event: PaymentNewEvent) -> None:
    try:
        async with AsyncSessionLocal() as session:
            payment = await session.get(Payment, event.payment_id)
            if payment is None:
                logger.error(
                    "Payment %s referenced by event not found, dropping message",
                    event.payment_id,
                )
                return

            if payment.status == PaymentStatus.PENDING:
                await _run_gateway_emulation(payment)
                await session.commit()

            if payment.webhook_delivered_at is None:
                await _send_webhook(payment)
                payment.webhook_delivered_at = datetime.now(timezone.utc)
                await session.commit()

        logger.info(
            "Payment %s fully processed (status=%s)", event.payment_id, payment.status.value
        )
    except Exception as exc:  # noqa: BLE001 - any failure funnels into retry/DLQ handling
        await _schedule_retry_or_dlq(event, exc)


@broker.subscriber(payments_dlq_queue, exchange=dlq_exchange)
async def handle_dead_letter(payload: dict) -> None:
    logger.error("DEAD LETTER received, payment event permanently failed: %s", payload)
