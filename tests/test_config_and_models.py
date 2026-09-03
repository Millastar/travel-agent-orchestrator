from __future__ import annotations

import pytest
from pydantic import ValidationError

from travel_agent_orchestrator.domain.models import AgentRequest, SessionStatus
from travel_agent_orchestrator.infrastructure.config import Settings


def test_settings_normalize_log_level_and_keep_numeric_log_size() -> None:
    settings = Settings(
        _env_file=None,
        log_level="debug",
        log_max_bytes=5 * 1024 * 1024,
        model_context_window_tokens=32_768,
        model_max_input_tokens=30_720,
    )

    assert settings.log_level == "DEBUG"
    assert isinstance(settings.log_max_bytes, int)
    assert settings.context_compression_ratio == 0.70
    assert settings.llm_request_timeout_seconds == 90
    assert settings.llm_max_retries == 2
    assert settings.max_read_only_tool_calls_per_agent == 8
    assert settings.graph_recursion_limit == 80
    assert settings.debug is True


def test_model_transport_settings_are_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_request_timeout_seconds=0)

    with pytest.raises(ValidationError):
        Settings(llm_max_retries=6)

    with pytest.raises(ValidationError):
        Settings(max_read_only_tool_calls_per_agent=1)

    with pytest.raises(ValidationError):
        Settings(graph_recursion_limit=24)


def test_database_pool_bounds_are_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(db_pool_min_size=5, db_pool_max_size=2)


def test_context_budget_rejects_unsafe_small_values() -> None:
    with pytest.raises(ValidationError):
        Settings(model_context_window_tokens=4_096, model_max_input_tokens=8_192)


def test_invalid_application_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(app_timezone="Mars/Olympus_Mons")


def test_agent_request_preserves_public_wire_fields() -> None:
    request = AgentRequest(
        user_id="user-1",
        session_id="session-1",
        task_id="task-1",
        query="hello",
    )

    assert set(request.model_dump()) == {
        "user_id",
        "session_id",
        "task_id",
        "query",
        "system_message",
    }
    assert SessionStatus.INTERRUPTED.value == "interrupted"
