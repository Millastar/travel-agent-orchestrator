"""Environment-backed application settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"
    log_file: Path = Path("var/logs/travel-agent-orchestrator.log")
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 3

    host: str = "0.0.0.0"
    port: int = 8002
    api_base_url: str = "http://localhost:8002"
    http_timeout_seconds: float = Field(default=15.0, gt=0)
    poll_interval_seconds: float = Field(default=1.0, gt=0)
    poll_timeout_seconds: float = Field(default=600.0, gt=0)
    web_state_secret: str = Field(default="travel-agent-orchestrator-local", min_length=12)

    db_uri: str = (
        "postgresql://travel_agent:travel_agent@localhost:5433/travel_agent?sslmode=disable"
    )
    db_pool_min_size: int = Field(default=2, ge=1)
    db_pool_max_size: int = Field(default=10, ge=1)

    redis_url: str = "redis://localhost:6380/0"
    redis_key_prefix: str = "travel-agent-orchestrator"
    session_timeout_seconds: int = Field(default=300, gt=0)
    session_ttl_seconds: int = Field(default=3600, gt=0)
    task_ttl_seconds: int = Field(default=3600, gt=0)
    celery_broker_url: str = "redis://localhost:6380/0"

    llm_type: Literal["openai", "oneapi", "qwen", "ollama"] = "qwen"
    llm_request_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    model_context_window_tokens: int = Field(default=32_768, ge=4_096)
    model_max_input_tokens: int = Field(default=30_720, ge=2_048)
    context_compression_ratio: float = Field(default=0.70, ge=0.50, le=0.85)
    context_recent_turns: int = Field(default=5, ge=1, le=20)
    context_summary_max_tokens: int = Field(default=1_200, ge=128, le=8_192)
    tool_artifact_inline_max_bytes: int = Field(default=8_192, ge=1_024)
    tool_artifact_page_size: int = Field(default=5, ge=1, le=20)
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    oneapi_base_url: str | None = None
    oneapi_api_key: str | None = None
    dashscope_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_chat_model: str = "llama3.1:8b"
    ollama_embedding_model: str = "nomic-embed-text:latest"
    amap_maps_api_key: str | None = None
    default_chat_model: str = "qwen3.8-max"
    supervisor_model: str | None = None
    discovery_model: str | None = None
    booking_model: str | None = None
    service_model: str | None = None
    supervisor_confidence_threshold: float = Field(default=0.65, ge=0, le=1)
    max_handoffs_per_task: int = Field(default=6, ge=1, le=12)
    max_read_only_tool_calls_per_agent: int = Field(default=8, ge=2, le=30)
    graph_recursion_limit: int = Field(default=80, ge=25, le=200)
    sandbox_payment_mode: Literal["success", "decline", "fail_after_capture"] = "success"
    langsmith_tracing: bool = False
    langsmith_project: str = "travel-agent-orchestrator"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("db_pool_max_size")
    @classmethod
    def validate_pool_size(cls, value: int, info):
        min_size = info.data.get("db_pool_min_size", 1)
        if value < min_size:
            raise ValueError("DB_POOL_MAX_SIZE must be greater than or equal to DB_POOL_MIN_SIZE")
        return value

    @model_validator(mode="after")
    def validate_context_limits(self) -> Settings:
        if self.model_max_input_tokens > self.model_context_window_tokens:
            raise ValueError("MODEL_MAX_INPUT_TOKENS must not exceed MODEL_CONTEXT_WINDOW_TOKENS")
        return self

    @property
    def debug(self) -> bool:
        return self.app_env == "development" and self.log_level == "DEBUG"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings snapshot."""
    return Settings()
