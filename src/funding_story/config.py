from functools import lru_cache
from typing import Literal

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["local", "test", "dev", "prod"] = "local"
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5440
    db_name: str = "funding_ai"
    db_username: str = "funding_ai"
    db_password: str = "local-ai-only"
    db_sslmode: str | None = None
    db_pool_min_size: int = Field(default=1, ge=0)
    db_pool_max_size: int = Field(default=5, ge=1)
    db_pool_timeout_seconds: float = Field(default=10, gt=0)
    db_pool_max_idle_seconds: float = Field(default=300, gt=0)
    db_pool_max_lifetime_seconds: float = Field(default=1800, gt=0)
    db_checkpoint_pool_max_size: int = Field(default=3, ge=1)
    celery_broker_url: str = "redis://localhost:56379/0"
    ai_service_token: str
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    text_model: str = "gemini-3.8-flash"
    image_model: str = "gemini-3.1-flash-image"
    storage_dir: str = "./data/assets"
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_endpoint: str | None = None
    pretendard_font_path: str = ""
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "fundit-funding-story-local"

    @model_validator(mode="after")
    def validate_database_configuration(self):
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError("DB_POOL_MIN_SIZE는 DB_POOL_MAX_SIZE보다 클 수 없습니다.")
        if self.app_env in ("dev", "prod"):
            connection_values = (
                conninfo_to_dict(self.database_url)
                if self.database_url
                else {
                    "host": self.db_host,
                    "dbname": self.db_name,
                    "user": self.db_username,
                    "password": self.db_password,
                }
            )
            if any(
                not connection_values.get(key, "").strip() for key in ("host", "dbname", "user", "password")
            ):
                raise ValueError("dev/prod DB 접속 정보는 비어 있을 수 없습니다.")
            if connection_values.get("host") in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("dev/prod DB_HOST에는 localhost를 사용할 수 없습니다.")
            if connection_values.get("password") == "local-ai-only":
                raise ValueError("dev/prod DB_PASSWORD에는 로컬 예시값을 사용할 수 없습니다.")
        return self

    @property
    def database_dsn(self) -> str:
        """Return a psycopg connection string; DATABASE_URL is a temporary compatibility override."""
        if self.database_url:
            return self.database_url
        values: dict[str, str | int] = {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": self.db_name,
            "user": self.db_username,
            "password": self.db_password,
        }
        if self.db_sslmode:
            values["sslmode"] = self.db_sslmode
        return make_conninfo(**values)


@lru_cache
def settings():
    return Settings()
