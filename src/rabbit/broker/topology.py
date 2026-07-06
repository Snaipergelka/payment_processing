import aio_pika

from src.rabbit.broker.setup import (
    DLQ_EXCHANGE_NAME,
    DLQ_QUEUE_NAME,
    DLQ_ROUTING_KEY,
    MAIN_EXCHANGE_NAME,
    MAIN_QUEUE_NAME,
    MAIN_ROUTING_KEY,
    RETRY_EXCHANGE_NAME,
    RETRY_ROUTING_KEY_TEMPLATE,
)


async def declare_topology(
    rabbitmq_url: str,
    retry_base_delay_ms: int,
    main_queue_max_length: int,
    dlq_max_length: int,
) -> None:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    try:
        channel = await connection.channel()

        main_exchange = await channel.declare_exchange(
            MAIN_EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        retry_exchange = await channel.declare_exchange(
            RETRY_EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        dlq_exchange = await channel.declare_exchange(
            DLQ_EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        main_queue = await channel.declare_queue(
            MAIN_QUEUE_NAME,
            durable=True,
            arguments={
                "x-max-length": main_queue_max_length,
                "x-overflow": "reject-publish-dlx",
                "x-dead-letter-exchange": DLQ_EXCHANGE_NAME,
                "x-dead-letter-routing-key": DLQ_ROUTING_KEY,
            },
        )
        await main_queue.bind(main_exchange, routing_key=MAIN_ROUTING_KEY)

        dlq_queue = await channel.declare_queue(
            DLQ_QUEUE_NAME,
            durable=True,
            arguments={
                "x-max-length": dlq_max_length,
                "x-overflow": "reject-publish",
            },
        )
        await dlq_queue.bind(dlq_exchange, routing_key=DLQ_ROUTING_KEY)

        # retry.1 -> TTL = base delay (e.g. 2s), retry.2 -> TTL = 2x base delay (e.g. 4s)
        for attempt, multiplier in ((1, 1), (2, 2)):
            routing_key = RETRY_ROUTING_KEY_TEMPLATE.format(attempt=attempt)
            retry_queue = await channel.declare_queue(
                f"payments.new.retry.{attempt}",
                durable=True,
                arguments={
                    "x-message-ttl": retry_base_delay_ms * multiplier,
                    "x-dead-letter-exchange": MAIN_EXCHANGE_NAME,
                    "x-dead-letter-routing-key": MAIN_ROUTING_KEY,
                },
            )
            await retry_queue.bind(retry_exchange, routing_key=routing_key)
    finally:
        await connection.close()
