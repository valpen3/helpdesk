from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings
from sqlalchemy import URL


class Settings(BaseSettings):
    log_level: str = "INFO"

    db_host: str = "postgres-db"
    db_port: int = 5432
    db_name: str = "sla_db"
    db_user: str = "sla_user"
    db_password: SecretStr = SecretStr("")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    mq_host: str = "rabbitmq"
    mq_port: int = 5672
    mq_user: str = "sla-svc"
    mq_password: SecretStr = SecretStr("")
    mq_vhost: str = "/"
    mq_queue: str = "sla.q"
    mq_timeout: float = Field(default=5.0, gt=0, le=30)
    retry_delay: float = Field(default=5.0, gt=0, le=60)

    sla_time_scale: float = Field(default=1.0, gt=0, le=3600)
    sla_check_interval: float = Field(default=30.0, gt=0, le=3600)


settings = Settings()
