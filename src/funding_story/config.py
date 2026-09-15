from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql://funding_ai:local-ai-only@localhost:55432/funding_ai"
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


@lru_cache
def settings():
    return Settings()
