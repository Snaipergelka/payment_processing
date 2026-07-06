import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_session
from src.payments.dependencies import verify_api_key
from src.payments.schemas import PaymentAcceptedResponse, PaymentCreateRequest, PaymentDetailResponse
from src.payments.service import create_payment, get_payment

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(verify_api_key)],
)


@router.post(
    "",
    response_model=PaymentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_payment_endpoint(
    payload: PaymentCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: AsyncSession = Depends(get_session),
) -> PaymentAcceptedResponse:
    payment, _created = await create_payment(session, idempotency_key, payload)
    return PaymentAcceptedResponse(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


@router.get(
    "/{payment_id}",
    response_model=PaymentDetailResponse,
)
async def get_payment_endpoint(
    payment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> PaymentDetailResponse:
    payment = await get_payment(session, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return PaymentDetailResponse.from_payment(payment)
