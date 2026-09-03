"""Normalize LangGraph results for persistence and API delivery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from travel_agent_orchestrator.domain.models import AgentResponse, SessionStatus


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())
    return value


def build_agent_response(session_id: str, task_id: str, result: dict[str, Any]) -> AgentResponse:
    """Convert completed or interrupted graph output into the public response model."""
    if "__interrupt__" in result:
        interrupts = result.get("__interrupt__") or []
        first = interrupts[0] if interrupts else None
        interrupt_data = getattr(first, "value", first) or {}
        normalized = to_jsonable(interrupt_data)
        if isinstance(normalized, dict):
            normalized.setdefault("interrupt_type", "tool_approval")
        return AgentResponse(
            session_id=session_id,
            task_id=task_id,
            status=SessionStatus.INTERRUPTED,
            interrupt_data=normalized,
        )
    if result.get("execution_error"):
        return AgentResponse(
            session_id=session_id,
            task_id=task_id,
            status=SessionStatus.ERROR,
            message=str(result.get("final_response") or "工具执行失败，请检查参数后重试。"),
            result=to_jsonable(result),
        )
    return AgentResponse(
        session_id=session_id,
        task_id=task_id,
        status=SessionStatus.COMPLETED,
        result=to_jsonable(result),
    )


def filter_last_human_conversation(response: AgentResponse) -> dict[str, Any]:
    """Keep the latest user turn in the Celery result payload."""
    data = response.model_dump(mode="json")
    messages = (data.get("result") or {}).get("messages") or []
    last_human_index = -1
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("type") == "human":
            last_human_index = index
    if last_human_index >= 0:
        data["result"] = {"messages": messages[last_human_index:]}
    elif data.get("interrupt_data") is not None:
        data["result"] = {"interrupt_data": data["interrupt_data"]}
    else:
        data["result"] = {"messages": []}
    return data
