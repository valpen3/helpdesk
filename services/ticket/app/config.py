"""Настройки сервиса. Все значения берутся из переменных окружения
(в docker compose — из секции environment), имена без учёта регистра:
LOG_LEVEL → log_level, ENABLE_DOCS → enable_docs."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings
from sqlalchemy import URL


class Settings(BaseSettings):
    service_name: str = "ticket-service"
    log_level: str = "INFO"
    enable_docs: bool = False  # Swagger UI на /docs; включаем только для разработки и демо
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "tickets_db"
    db_user: str = "tickets_user"
    db_password: SecretStr = SecretStr("")
    cache_enabled: bool = True
    cache_timeout: float = Field(default=0.25, gt=0, le=5)
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    redis_user: str | None = None
    redis_password: SecretStr = SecretStr("")
    mq_host: str = "rabbitmq"
    mq_port: int = 5672
    mq_user: str = "ticket-svc"
    mq_password: SecretStr = SecretStr("")
    mq_vhost: str = "/"
    mq_timeout: float = Field(default=5.0, gt=0, le=30)

    @property
    def database_url(self) -> URL:
        # URL.create корректно обрабатывает @, %, : и другие символы в пароле.
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


settings = Settings()
