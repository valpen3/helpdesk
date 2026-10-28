"""Настройки сервиса. Все значения берутся из переменных окружения
(в docker compose — из секции environment), имена без учёта регистра:
LOG_LEVEL → log_level, ENABLE_DOCS → enable_docs."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "ticket-service"
    log_level: str = "INFO"
    enable_docs: bool = False  # Swagger UI на /docs; включаем только для разработки и демо


settings = Settings()
