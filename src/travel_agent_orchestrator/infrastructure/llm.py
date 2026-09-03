"""LLM and embedding client construction."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from travel_agent_orchestrator.infrastructure.config import Settings

logger = logging.getLogger(__name__)


class LLMInitializationError(RuntimeError):
    """Raised when the selected provider is missing required configuration."""


@dataclass(frozen=True)
class ModelConfiguration:
    base_url: str | None
    api_key: str
    chat_model: str
    embedding_model: str


def _provider_configuration(settings: Settings) -> ModelConfiguration:
    configurations = {
        "openai": ModelConfiguration(
            settings.openai_base_url,
            settings.openai_api_key or "",
            settings.default_chat_model if settings.llm_type == "openai" else "gpt-4o-mini",
            "text-embedding-3-small",
        ),
        "oneapi": ModelConfiguration(
            settings.oneapi_base_url,
            settings.oneapi_api_key or "",
            settings.default_chat_model,
            "text-embedding-v1",
        ),
        "qwen": ModelConfiguration(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            settings.dashscope_api_key or "",
            settings.default_chat_model,
            "text-embedding-v1",
        ),
        "ollama": ModelConfiguration(
            settings.ollama_base_url,
            "ollama",
            settings.ollama_chat_model,
            settings.ollama_embedding_model,
        ),
    }
    configuration = configurations[settings.llm_type]
    if not configuration.api_key:
        variable = {
            "openai": "OPENAI_API_KEY",
            "oneapi": "ONEAPI_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
        }.get(settings.llm_type, "API key")
        raise LLMInitializationError(f"{variable} is required for provider {settings.llm_type!r}")
    if settings.llm_type == "oneapi" and not configuration.base_url:
        raise LLMInitializationError("ONEAPI_BASE_URL is required for provider 'oneapi'")
    return configuration


def get_chat_model(settings: Settings, role: str | None = None) -> ChatOpenAI:
    """Create a role-specific chat client while sharing provider credentials."""
    configuration = _provider_configuration(settings)
    override = {
        "supervisor": settings.supervisor_model,
        "discovery": settings.discovery_model,
        "booking": settings.booking_model,
        "customer_service": settings.service_model,
    }.get(role or "")
    if override:
        configuration = replace(configuration, chat_model=override)
    chat = ChatOpenAI(
        base_url=configuration.base_url,
        api_key=configuration.api_key,
        model=configuration.chat_model,
        temperature=0,
        timeout=settings.llm_request_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    logger.info(
        "Initialized chat model provider=%s role=%s model=%s",
        settings.llm_type,
        role or "default",
        configuration.chat_model,
    )
    return chat


def get_llm(settings: Settings) -> tuple[ChatOpenAI, OpenAIEmbeddings]:
    """Create clients without logging credentials or endpoint query parameters."""
    configuration = _provider_configuration(settings)
    try:
        chat = get_chat_model(settings)
        embeddings = OpenAIEmbeddings(
            base_url=configuration.base_url,
            api_key=configuration.api_key,
            model=configuration.embedding_model,
            deployment=configuration.embedding_model,
        )
    except Exception as exc:  # pragma: no cover - provider-specific validation
        raise LLMInitializationError(
            f"Unable to initialize provider {settings.llm_type!r}"
        ) from exc
    logger.info("Initialized LLM provider=%s model=%s", settings.llm_type, configuration.chat_model)
    return chat, embeddings
