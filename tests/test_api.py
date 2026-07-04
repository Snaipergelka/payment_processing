import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Payment
from tests.conftest import new_idempotency_key, valid_payment_payload

PAYMENTS_URL = "/api/v1/payments"


async def test_create_payment_requires_api_key(api_client):
    response = await api_client.post(
        PAYMENTS_URL,
        json=valid_payment_payload(),
        headers={"Idempotency-Key": new_idempotency_key()},
    )
    assert response.status_code == 401


async def test_create_payment_rejects_wrong_api_key(api_client):
    response = await api_client.post(
        PAYMENTS_URL,
        json=valid_payment_payload(),
        headers={"Idempotency-Key": new_idempotency_key(), "X-API-Key": "definitely-wrong"},
    )
    assert response.status_code == 401


async def test_get_payment_requires_api_key(api_client):
    response = await api_client.get(f"{PAYMENTS_URL}/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_get_payment_rejects_wrong_api_key(api_client):
    response = await api_client.get(
        f"{PAYMENTS_URL}/{uuid.uuid4()}", headers={"X-API-Key": "nope"}
    )
    assert response.status_code == 401


async def test_create_payment_happy_path(api_client, auth_headers):
    headers = {**auth_headers, "Idempotency-Key": new_idempotency_key()}
    response = await api_client.post(PAYMENTS_URL, json=valid_payment_payload(), headers=headers)

    assert response.status_code == 202
    body = response.json()
    assert uuid.UUID(body["payment_id"])
    assert body["status"] == "pending"
    assert "created_at" in body


async def test_duplicate_idempotency_key_does_not_create_a_second_payment(
    api_client, auth_headers, db_session
):
    key = new_idempotency_key()
    headers = {**auth_headers, "Idempotency-Key": key}

    first = await api_client.post(PAYMENTS_URL, json=valid_payment_payload(), headers=headers)
    second = await api_client.post(
        PAYMENTS_URL, json=valid_payment_payload(amount="999.00"), headers=headers
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["payment_id"] == second.json()["payment_id"]

    result = await db_session.execute(select(Payment).where(Payment.idempotency_key == key))
    assert len(result.scalars().all()) == 1


async def test_different_idempotency_keys_are_independent(api_client, auth_headers):
    response_a = await api_client.post(
        PAYMENTS_URL,
        json=valid_payment_payload(),
        headers={**auth_headers, "Idempotency-Key": new_idempotency_key()},
    )
    response_b = await api_client.post(
        PAYMENTS_URL,
        json=valid_payment_payload(),
        headers={**auth_headers, "Idempotency-Key": new_idempotency_key()},
    )
    assert response_a.json()["payment_id"] != response_b.json()["payment_id"]


async def test_get_unknown_payment_returns_404(api_client, auth_headers):
    response = await api_client.get(f"{PAYMENTS_URL}/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


async def test_get_payment_with_malformed_uuid_returns_422(api_client, auth_headers):
    response = await api_client.get(f"{PAYMENTS_URL}/not-a-uuid", headers=auth_headers)
    assert response.status_code == 422


async def test_get_payment_returns_full_detail_with_correct_field_mapping(
    api_client, auth_headers
):
    headers = {**auth_headers, "Idempotency-Key": new_idempotency_key()}
    create_response = await api_client.post(
        PAYMENTS_URL,
        json=valid_payment_payload(metadata={"order_id": 42}),
        headers=headers,
    )
    payment_id = create_response.json()["payment_id"]

    get_response = await api_client.get(f"{PAYMENTS_URL}/{payment_id}", headers=auth_headers)

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["payment_id"] == payment_id
    assert body["status"] == "pending"
    assert Decimal(body["amount"]) == Decimal("100.50")
    assert body["currency"] == "RUB"
    assert body["metadata"] == {"order_id": 42}
    assert body["idempotency_key"] == headers["Idempotency-Key"]
    assert body["webhook_delivered_at"] is None
    assert body["processed_at"] is None
