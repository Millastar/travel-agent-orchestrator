"""Pure transformations between API payloads and presentation models."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

INTERRUPT_PROMPT = "我已准备调用所需工具。请检查下方工具及参数，确认后继续。"


@dataclass(frozen=True)
class TaskView:
    task_id: str
    status: str
    query: str
    updated_at: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class SessionView:
    session_id: str
    title: str
    updated_at: float
    tasks: tuple[TaskView, ...]

    @property
    def choice_label(self) -> str:
        stamp = time.strftime("%m-%d %H:%M", time.localtime(self.updated_at))
        return f"{self.title}\n{stamp}"


STATUS_LABELS = {
    "idle": "准备就绪",
    "pending": "任务已排队",
    "running": "智能体正在处理",
    "interrupted": "等待人工审批",
    "completed": "任务已完成",
    "error": "执行失败",
    "failed": "执行失败",
    "not_found": "任务已过期",
}


def split_task_status(value: str) -> tuple[str, str]:
    task_id, separator, status = value.rpartition(":")
    return (task_id, status) if separator else (value, "unknown")


def task_from_payload(task_id: str, fallback_status: str, payload: dict[str, Any]) -> TaskView:
    updated = payload.get("last_updated")
    return TaskView(
        task_id=task_id,
        status=str(payload.get("status") or fallback_status),
        query=str(payload.get("last_query") or "").strip(),
        updated_at=float(updated) if isinstance(updated, int | float) else 0.0,
        payload=payload,
    )


def session_title(tasks: list[TaskView], session_id: str) -> str:
    latest_query = next((task.query for task in reversed(tasks) if task.query), "")
    compact = " ".join(latest_query.split())
    if compact:
        return f"{compact[:24]}{'…' if len(compact) > 24 else ''}"
    return f"会话 {session_id[:8]}"


def extract_answer(payload: dict[str, Any]) -> str | None:
    response = payload.get("last_response") or payload
    result = response.get("result") or {}
    messages = result.get("messages") or []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text_parts = [
                str(item.get("text"))
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            if text_parts:
                return "\n".join(text_parts)
    return None


def extract_error(payload: dict[str, Any]) -> str:
    response = payload.get("last_response") or {}
    return str(
        response.get("message")
        or payload.get("message")
        or "智能体执行失败，请检查服务配置后重试。"
    )


def extract_interrupt(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    response = payload.get("last_response") or payload
    interrupt = response.get("interrupt_data") or {}
    request = interrupt.get("action_request") or {}
    name = str(request.get("action") or "未知工具")
    description = str(interrupt.get("description") or "此工具需要你的确认后才能执行。")
    arguments = request.get("args")
    return name, description, arguments if isinstance(arguments, dict) else {}


def format_interrupt_task_state(payload: dict[str, Any]) -> str:
    """Render durable objective, progress, and constraints for the approval card."""
    response = payload.get("last_response") or payload
    interrupt = response.get("interrupt_data") or {}
    task_state = interrupt.get("task_state") or {}
    if not isinstance(task_state, dict):
        return ""
    objective = str(task_state.get("objective") or "").strip()
    plan = task_state.get("plan") or []
    constraints = task_state.get("constraints") or {}
    sections: list[str] = []
    if objective:
        sections.append(f"**当前目标**\n\n{objective}")
    if isinstance(plan, list) and plan:
        symbols = {
            "completed": "✓",
            "waiting_approval": "●",
            "in_progress": "●",
            "rejected": "×",
            "failed": "!",
            "pending": "○",
        }
        progress = []
        for item in plan:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "pending")
            progress.append(f"- {symbols.get(status, '○')} {item.get('step') or '任务步骤'}")
        if progress:
            sections.append("**执行进度**\n\n" + "\n".join(progress))
    if isinstance(constraints, dict) and constraints:
        sections.append(
            "**已确认约束**\n\n```json\n"
            + json.dumps(constraints, ensure_ascii=False, indent=2)
            + "\n```"
        )
    return "\n\n".join(sections)


def build_chat_messages(tasks: list[TaskView]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for task in sorted(tasks, key=lambda item: item.updated_at):
        if task.query:
            messages.append({"role": "user", "content": task.query})
        if task.status == "completed":
            answer = extract_answer(task.payload)
            messages.append(
                {
                    "role": "assistant",
                    "content": answer or "任务已完成，但没有可显示的文本结果。",
                    "metadata": {"title": extract_agent_label(task.payload)},
                }
            )
        elif task.status == "interrupted":
            messages.append({"role": "assistant", "content": INTERRUPT_PROMPT})
        elif task.status in {"error", "failed"}:
            messages.append(
                {
                    "role": "assistant",
                    "content": f"⚠️ {extract_error(task.payload)}",
                    "metadata": {"title": "执行未完成"},
                }
            )
    return messages


def extract_agent_label(payload: dict[str, Any]) -> str:
    """Return a stable UI label for the specialist that produced a response."""
    response = payload.get("last_response") or payload.get("result") or payload
    result = response.get("result") if isinstance(response, dict) else None
    role = result.get("active_agent") if isinstance(result, dict) else None
    if not role and isinstance(response, dict):
        interrupt_data = response.get("interrupt_data") or {}
        role = interrupt_data.get("agent_role") if isinstance(interrupt_data, dict) else None
    return {
        "supervisor": "SUPERVISOR",
        "discovery": "DISCOVERY AGENT",
        "booking": "BOOKING AGENT",
        "customer_service": "CUSTOMER SERVICE",
        "direct": "SUPERVISOR",
    }.get(str(role), "TRAVEL AGENT")


def arguments_to_rows(arguments: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for key, value in arguments.items():
        display = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        rows.append([str(key), str(display)])
    return rows


def parse_argument_rows(rows: Any) -> dict[str, Any]:
    if hasattr(rows, "values"):
        rows = rows.values.tolist()
    if not isinstance(rows, list):
        raise ValueError("参数表格格式无效。")

    parsed: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, list | tuple) or len(row) < 2:
            continue
        key = str(row[0] or "").strip()
        if not key:
            raise ValueError("参数名称不能为空。")
        raw = row[1]
        if not isinstance(raw, str):
            parsed[key] = raw
            continue
        try:
            parsed[key] = json.loads(raw)
        except json.JSONDecodeError:
            parsed[key] = raw
    return parsed


def parse_json_arguments(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError as exc:
        raise ValueError("请输入有效的 JSON 参数。") from exc
    if not isinstance(parsed, dict):
        raise ValueError("工具参数必须是 JSON 对象。")
    return parsed


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, "状态未知")


def polling_timed_out(
    started_at: float, timeout_seconds: float, *, now: float | None = None
) -> bool:
    current = time.time() if now is None else now
    return started_at > 0 and current - started_at >= timeout_seconds
