from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

from src.config import get_settings

settings = get_settings()

broker = RabbitBroker(settings.rabbitmq_url, max_consumers=settings.consumer_prefetch_count)

MAIN_EXCHANGE_NAME = "payments"
RETRY_EXCHANGE_NAME = "payments.retry"
DLQ_EXCHANGE_NAME = "payments.dlq"

MAIN_ROUTING_KEY = "payments.new"
RETRY_ROUTING_KEY_TEMPLATE = "payments.new.retry.{attempt}"
DLQ_ROUTING_KEY = "payments.new.dlq"

MAIN_QUEUE_NAME = "payments.new"
DLQ_QUEUE_NAME = "payments.new.dlq"

main_exchange = RabbitExchange(MAIN_EXCHANGE_NAME, type=ExchangeType.TOPIC, durable=True)
retry_exchange = RabbitExchange(RETRY_EXCHANGE_NAME, type=ExchangeType.TOPIC, durable=True)
dlq_exchange = RabbitExchange(DLQ_EXCHANGE_NAME, type=ExchangeType.TOPIC, durable=True)

payments_new_queue = RabbitQueue(
    MAIN_QUEUE_NAME,
    routing_key=MAIN_ROUTING_KEY,
    durable=True,
    arguments={
        "x-max-length": settings.main_queue_max_length,
        "x-overflow": "reject-publish-dlx",
        "x-dead-letter-exchange": DLQ_EXCHANGE_NAME,
        "x-dead-letter-routing-key": DLQ_ROUTING_KEY,
    },
)


def _retry_queue(attempt: int, ttl_ms: int) -> RabbitQueue:
    routing_key = RETRY_ROUTING_KEY_TEMPLATE.format(attempt=attempt)
    return RabbitQueue(
        f"payments.new.retry.{attempt}",
        routing_key=routing_key,
        durable=True,
        arguments={
            "x-message-ttl": ttl_ms,
            "x-dead-letter-exchange": MAIN_EXCHANGE_NAME,
            "x-dead-letter-routing-key": MAIN_ROUTING_KEY,
        },
    )


retry_queue_1 = _retry_queue(1, settings.retry_base_delay_ms)
retry_queue_2 = _retry_queue(2, settings.retry_base_delay_ms * 2)

RETRY_QUEUES = {1: retry_queue_1, 2: retry_queue_2}
RETRY_ROUTING_KEYS = {
    1: RETRY_ROUTING_KEY_TEMPLATE.format(attempt=1),
    2: RETRY_ROUTING_KEY_TEMPLATE.format(attempt=2),
}

payments_dlq_queue = RabbitQueue(
    DLQ_QUEUE_NAME,
    routing_key=DLQ_ROUTING_KEY,
    durable=True,
    arguments={
        "x-max-length": settings.dlq_max_length,
        "x-overflow": "reject-publish",
    },
)
