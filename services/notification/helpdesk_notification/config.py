from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    log_level: str = "INFO"
    mq_host: str = "rabbitmq"
    mq_port: int = 5672
    mq_user: str = "notif-svc"
    mq_password: SecretStr = SecretStr("")
    mq_vhost: str = "/"
    mq_queue: str = "notifications.q"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    mail_from: str = "helpdesk@helpdesk.local"
    io_timeout: float = Field(default=5.0, gt=0)
    retry_delay: float = Field(default=5.0, gt=0)
