"""Persistent state and deterministic task lifecycle helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    system_prompt: str
    long_term_memory: str
    conversation_summary: str
    summary_cursor: str | None
    key_facts: list[str]
    recent_message_ids: list[str]
    task_plan: dict[str, Any]
    pending_approval: dict[str, Any] | None
    tool_call_queue: list[dict[str, Any]]
    tool_call_history: list[str]
    tool_call_counts: dict[str, int]
    approval_decision: dict[str, Any] | None
    tool_artifact_refs: list[dict[str, Any]]
    user_id: str
    session_id: str
    task_id: str
    trace_id: str
    trace_sequence: int
    active_agent: str
    route_decision: dict[str, Any]
    handoff_history: list[dict[str, Any]]
    travel_workflow: dict[str, Any]
    final_response: str | None
    execution_error: str | None


def new_task_plan(objective: str) -> dict[str, Any]:
    return {
        "objective": objective,
        "steps": [
            {"step": "理解请求", "status": "in_progress"},
            {"step": "执行所需操作", "status": "pending"},
            {"step": "整理并返回结果", "status": "pending"},
        ],
        "constraints": {},
    }


def update_task_plan(
    plan: dict[str, Any] | None,
    *,
    operation_status: str | None = None,
    operation_label: str | None = None,
    final_status: str | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = deepcopy(plan or new_task_plan("处理当前请求"))
    steps = list(updated.get("steps") or [])
    while len(steps) < 3:
        steps.append({"step": "处理任务", "status": "pending"})
    steps[0]["status"] = "completed"
    if operation_label is not None:
        steps[1]["step"] = operation_label
    if operation_status is not None:
        steps[1]["status"] = operation_status
    if final_status is not None:
        steps[2]["status"] = final_status
    updated["steps"] = steps
    if constraints is not None:
        updated["constraints"] = deepcopy(constraints)
    return updated


def task_state_for_public(state: AgentState) -> dict[str, Any]:
    return {
        "objective": str((state.get("task_plan") or {}).get("objective") or ""),
        "plan": list((state.get("task_plan") or {}).get("steps") or []),
        "constraints": dict((state.get("task_plan") or {}).get("constraints") or {}),
    }
