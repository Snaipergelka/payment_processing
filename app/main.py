import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.payments import router as payments_router
from app.broker import broker
from app.outbox.relay import outbox_relay_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await broker.connect()
    relay_task = asyncio.create_task(outbox_relay_loop())
    logger.info("API started, outbox relay running")
    try:
        yield
    finally:
        relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay_task
        await broker.close()


app = FastAPI(
    title="Payment Processing Service",
    description="Asynchronous payment processing microservice (outbox + RabbitMQ + webhooks).",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(payments_router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}
