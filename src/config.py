from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    api_key: str = "super-secret-api-key"
    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 20
    stale_pending_threshold_seconds: float = 60.0
    max_stale_requeue_attempts: int = 3
    consumer_prefetch_count: int = 10
    main_queue_max_length: int = 10_000
    dlq_max_length: int = 50_000
    max_delivery_attempts: int = 3
    retry_base_delay_ms: int = 2000
    webhook_timeout_seconds: float = 5.0
    gateway_min_delay_seconds: float = 2.0
    gateway_max_delay_seconds: float = 5.0
    gateway_failure_rate: float = 0.1


@lru_cache
def get_settings() -> Settings:
    return Settings()
