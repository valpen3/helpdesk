"""Настройки сервиса. Все значения берутся из переменных окружения
(в docker compose — из секции environment), имена без учёта регистра:
LOG_LEVEL → log_level, ENABLE_DOCS → enable_docs."""

from pydantic import SecretStr
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
