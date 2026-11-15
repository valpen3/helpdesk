from pydantic import EmailStr, Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    log_level: str = "INFO"
    mq_host: str = "rabbitmq"
    mq_port: int = 5672
    mq_user: str = "notif-svc"
    mq_password: SecretStr = SecretStr("")
    mq_vhost: str = "/"
    mq_queue: str = "notifications.q"
    retry_exchange: str = "notifications.retry"
    dead_exchange: str = "notifications.dead"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    io_timeout: float = Field(default=5.0, gt=0)
    retry_delay: float = Field(default=5.0, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=10, validation_alias="NOTIF_MAX_ATTEMPTS")
    idempotency_ttl: int = Field(
        default=604800, ge=60, validation_alias="NOTIF_IDEMPOTENCY_TTL"
    )
    idempotency_lock_ttl: int = Field(
        default=300, ge=10, validation_alias="NOTIF_IDEMPOTENCY_LOCK_TTL"
    )
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_user: str = "notif-svc"
    redis_password: SecretStr = SecretStr("")
    oncall_email: EmailStr = "oncall@helpdesk.local"
    admin_notify_email: EmailStr = "admin@helpdesk.local"
    mail_from: EmailStr = "helpdesk@helpdesk.local"
