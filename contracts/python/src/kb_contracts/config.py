"""C11: typed settings. Safe defaults: real send OFF, shadow mode, all providers mock."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kb_contracts.enums import AutonomyMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://backlink:backlink@localhost:55432/backlink"
    qdrant_url: str = "http://localhost:56333"
    ollama_url: str = "http://localhost:11434"
    real_send_enabled: bool = False
    autonomy_mode: AutonomyMode = AutonomyMode.SHADOW
    default_daily_send_limit: int = Field(default=25, ge=0)
    email_provider: str = "mock"
    seo_metrics_provider: str = "mock"
    search_provider: str = "mock"
    crawler_provider: str = "mock"
    # Names stay configurable; neither becomes a production default before evaluation (ADR-0004).
    reasoning_model: str = "qwen3:8b-q4_K_M"
    embedding_model: str = "nomic-embed-text"
    api_token: SecretStr | None = Field(default=None, repr=False)  # never from env in prod

    @model_validator(mode="after")
    def _real_send_is_never_a_shadow_setting(self) -> Settings:
        # Fail closed: an env/.env typo cannot enable sending while in shadow mode. The runtime
        # gate (approved live_send_activation + kill switch, ADR-0007) is enforced by the
        # PolicyEngine in Lane B; this is only the configuration-level backstop.
        if self.real_send_enabled and self.autonomy_mode == AutonomyMode.SHADOW:
            raise ValueError(
                "REAL_SEND_ENABLED requires AUTONOMY_MODE guarded or autonomous (ADR-0007)"
            )
        return self

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg")
