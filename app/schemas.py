from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, HttpUrl

from app.models import Currency, PaymentStatus

if TYPE_CHECKING:
    from app.models import Payment


class PaymentCreateRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, description="Payment amount, must be positive")
    currency: Currency
    description: str | None = Field(default=None, max_length=1000)
    metadata: dict | None = Field(default=None, description="Arbitrary extra information")
    webhook_url: HttpUrl = Field(..., description="URL notified with the processing result")


class PaymentAcceptedResponse(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentDetailResponse(BaseModel):
    payment_id: uuid.UUID
    amount: Decimal
    currency: Currency
    description: str | None
    metadata: dict | None
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    webhook_delivered_at: datetime | None
    created_at: datetime
    processed_at: datetime | None

    @classmethod
    def from_payment(cls, payment: "Payment") -> "PaymentDetailResponse":
        return cls(
            payment_id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            description=payment.description,
            metadata=payment.metadata_,
            status=payment.status,
            idempotency_key=payment.idempotency_key,
            webhook_url=payment.webhook_url,
            webhook_delivered_at=payment.webhook_delivered_at,
            created_at=payment.created_at,
            processed_at=payment.processed_at,
        )


class ErrorResponse(BaseModel):
    detail: str
