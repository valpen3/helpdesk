"""Environment-backed settings for auth-service."""

from pydantic import EmailStr, Field, SecretStr
from pydantic_settings import BaseSettings
from sqlalchemy import URL


class Settings(BaseSettings):
    service_name: str = "auth-service"
    log_level: str = "INFO"
    enable_docs: bool = False
    db_host: str = "postgres-db"
    db_port: int = 5432
    db_name: str = "auth_db"
    db_user: str = "auth_user"
    db_password: SecretStr = SecretStr("")
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_user: str = "auth-svc"
    redis_password: SecretStr = SecretStr("")
    cache_timeout: float = Field(default=0.25, gt=0, le=5)
    jwt_private_key_path: str = "/run/secrets/jwt_private.pem"
    jwt_public_key_path: str = "/run/secrets/jwt_public.pem"
    access_token_ttl_min: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=90)
    admin_email: EmailStr = "admin@helpdesk.local"
    admin_password: SecretStr = SecretStr("")
    login_max_attempts: int = Field(default=5, ge=1, le=20)
    login_lock_seconds: int = Field(default=900, ge=60, le=3600)

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


settings = Settings()
