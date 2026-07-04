import asyncio
import logging

from app.config import get_settings
from app.rabbitmq_topology import declare_topology

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    await declare_topology(
        settings.rabbitmq_url,
        settings.retry_base_delay_ms,
        settings.main_queue_max_length,
        settings.dlq_max_length,
    )
    logger.info("RabbitMQ topology declared (exchanges, queues, bindings, DLQ).")


if __name__ == "__main__":
    asyncio.run(main())
