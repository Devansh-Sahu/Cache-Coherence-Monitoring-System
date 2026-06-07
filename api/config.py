"""
Configuration management for Cache Staleness Monitor.
All settings loaded from environment variables / .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── PostgreSQL / PGVector ─────────────────────────────────────────────────
    database_url: str = "postgresql://csm_user:csm_password@localhost:5432/csm_db"
    pgvector_embedding_dim: int = 384  # all-MiniLM-L6-v2

    # ── Groq LLM ──────────────────────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"   # fast + powerful, free tier
    groq_max_tokens: int = 500

    # ── Slack ──────────────────────────────────────────────────────────────────
    slack_webhook_url: str = ""
    slack_channel: str = "#cache-alerts"

    # ── AWS ───────────────────────────────────────────────────────────────────
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_default_region: str = "us-east-1"
    use_localstack: bool = True
    aws_endpoint_url: str = "http://localhost:4566"

    # ── DynamoDB ──────────────────────────────────────────────────────────────
    dynamodb_registry_table: str = "CacheKeyRegistry"
    dynamodb_history_table: str = "StalenessHistory"

    # ── S3 ────────────────────────────────────────────────────────────────────
    s3_runbooks_bucket: str = "csm-runbooks"

    # ── SQS ───────────────────────────────────────────────────────────────────
    sqs_alert_queue_url: str = ""

    # ── Worker ────────────────────────────────────────────────────────────────
    staleness_check_interval_seconds: int = 30
    sla_breach_multiplier: float = 1.5
    auto_tag_value_max_chars: int = 200

    @field_validator("groq_api_key")
    @classmethod
    def warn_missing_api_key(cls, v: str) -> str:
        if not v:
            import warnings
            warnings.warn(
                "GROQ_API_KEY not set — LLM calls will return mock responses",
                stacklevel=2,
            )
        return v

    @property
    def dynamodb_endpoint(self) -> str | None:
        return self.aws_endpoint_url if self.use_localstack else None

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
