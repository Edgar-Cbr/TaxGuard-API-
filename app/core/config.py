from typing import Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application Information
    PROJECT_NAME: str = "TaxGuard API"
    VERSION: str = "1.1.0"
    ENVIRONMENT: Literal["development", "staging", "production", "testing"] = (
        "development"
    )
    DEBUG: bool = False
    API_V1_STR: str = "/api/v1"

    # Security & JWT
    SECRET_KEY: str = (
        "super_secret_taxguard_key_for_development_purposes_only_replace_in_prod"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    STRIPE_WEBHOOK_SECRET: str = "whsec_taxguard_dev_secret_key"

    # Database (PostgreSQL 16 via asyncpg)
    DATABASE_URL: str = (
        "postgresql+asyncpg://taxguard:taxguard_secret@localhost:5432/taxguard_db"
    )

    # Redis (Redis 7 Alpine via redis.asyncio)
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: list[str] | str = ["*"]

    @field_validator("CORS_ORIGINS", mode="after")
    @classmethod
    def assemble_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            if v.strip().startswith("[") and v.strip().endswith("]"):
                # Handled by default json deserializer if formatted as json
                import json

                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    pass
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return ["*"]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "testing"


settings = Settings()
