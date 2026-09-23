from functools import lru_cache
from typing import Literal

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODEL_PROFILE_DEFAULTS = {
    "runtime": {
        "text_model": "gemini-3.8-flash",
        "image_provider": "openai",
        "image_model": "gpt-image-2.5-flare",
        "image_quality": "medium",
        "openai_auth_mode": "eks_wif",
    },
    "local_openai_smoke": {
        "text_model": "gemini-3.8-flash",
        "image_provider": "openai",
        "image_model": "gpt-image-2.5-flare",
        "image_quality": "medium",
        "openai_auth_mode": "api_key",
    },
    "local_google_experiment": {
        "text_model": "gemini-3.8-flash",
        "image_provider": "google",
        "image_model": "gemini-3.1-flash-image",
        "image_quality": "auto",
        "openai_auth_mode": "api_key",
    },
}


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
    funding_story_state_ttl_seconds: int = Field(default=86_400, ge=300)
    project_service_base_url: str = "http://127.0.0.1:58002"
    internal_api_key: str = ""
    internal_http_timeout_seconds: float = Field(default=20, gt=0)
    completion_callback_attempts: int = Field(default=3, ge=1, le=5)
    ai_service_token: str
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    model_profile: Literal["runtime", "local_openai_smoke", "local_google_experiment"] = (
        "local_google_experiment"
    )
    text_model: str = "gemini-3.8-flash"
    image_provider: Literal["openai", "google"] = "google"
    image_model: str = "gemini-3.1-flash-image"
    image_quality: Literal["low", "medium", "high", "xhigh", "max", "auto"] = "auto"
    openai_auth_mode: Literal["eks_wif", "api_key"] = "api_key"
    openai_identity_provider_id: str = ""
    openai_service_account_id: str = ""
    openai_wif_audience: str = ""
    openai_wif_token_file: str = ""
    openai_api_key: SecretStr | None = None
    openai_project: str = ""
    openai_organization: str = ""
    image_generation_concurrency: int = Field(default=4, ge=1, le=8)
    image_generation_attempts: int = Field(default=5, ge=1, le=5)
    image_retry_delay_seconds: float = Field(default=2, gt=0, le=120)
    image_retry_max_delay_seconds: float = Field(default=30, gt=0, le=120)
    image_request_interval_seconds: float = Field(default=0.25, ge=0, le=60)
    image_request_max_interval_seconds: float = Field(default=20, gt=0, le=120)
    image_generation_budget_seconds: float = Field(default=3600, gt=0, le=7200)
    render_concurrency: int = Field(default=2, ge=1, le=4)
    pretendard_font_path: str = ""

    @model_validator(mode="before")
    @classmethod
    def apply_model_profile_defaults(cls, values):
        data = dict(values or {})
        profile = data.get("model_profile", "local_google_experiment")
        defaults = MODEL_PROFILE_DEFAULTS.get(profile)
        if defaults:
            for key, value in defaults.items():
                data.setdefault(key, value)
        return data

    @model_validator(mode="after")
    def validate_configuration(self):
        profile = MODEL_PROFILE_DEFAULTS[self.model_profile]
        fixed_fields = ("text_model", "image_provider", "image_quality", "openai_auth_mode")
        if self.model_profile != "local_google_experiment":
            fixed_fields += ("image_model",)
        mismatched = [
            key for key in fixed_fields if getattr(self, key) != profile[key]
        ]
        if mismatched:
            raise ValueError(
                f"MODEL_PROFILE={self.model_profile}과 설정이 일치하지 않습니다: {', '.join(mismatched)}"
            )
        if self.app_env == "local" and self.model_profile == "runtime":
            raise ValueError(
                "local에서는 local_openai_smoke 또는 local_google_experiment를 사용하세요."
            )
        if self.image_provider == "openai" and not self.image_model.startswith("gpt-image-"):
            raise ValueError("OpenAI 이미지 Provider에는 gpt-image 모델이 필요합니다.")
        if self.image_provider == "google" and not self.image_model.startswith("gemini-"):
            raise ValueError("Google 이미지 Provider에는 gemini 이미지 모델이 필요합니다.")
        if self.image_request_max_interval_seconds < self.image_request_interval_seconds:
            raise ValueError("이미지 최대 요청 간격은 기본 간격보다 작을 수 없습니다.")
        if self.image_retry_max_delay_seconds < self.image_retry_delay_seconds:
            raise ValueError("이미지 최대 재시도 대기는 기본 대기보다 작을 수 없습니다.")
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
        if self.app_env in ("dev", "prod") and self.model_profile != "runtime":
            raise ValueError("dev/prod에는 MODEL_PROFILE=runtime만 사용할 수 있습니다.")
        if self.app_env in ("dev", "prod") and self.image_provider == "openai":
            if self.openai_auth_mode != "eks_wif":
                raise ValueError("dev/prod OpenAI 이미지 Provider 인증은 EKS WIF만 사용할 수 있습니다.")
            required = {
                "OPENAI_IDENTITY_PROVIDER_ID": self.openai_identity_provider_id,
                "OPENAI_SERVICE_ACCOUNT_ID": self.openai_service_account_id,
                "OPENAI_WIF_AUDIENCE": self.openai_wif_audience,
                "OPENAI_WIF_TOKEN_FILE": self.openai_wif_token_file,
            }
            missing = [name for name, value in required.items() if not value.strip()]
            if missing:
                raise ValueError(f"dev/prod OpenAI WIF 설정이 필요합니다: {', '.join(missing)}")
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
