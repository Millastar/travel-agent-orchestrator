"""Session-oriented orchestration used by Gradio callbacks."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from travel_agent_orchestrator.web.presentation import (
    SessionView,
    TaskView,
    build_chat_messages,
    session_title,
    split_task_status,
    task_from_payload,
)


class AgentClient(Protocol):
    def get_active_session(self, user_id: str) -> dict[str, Any]: ...

    def get_sessions(self, user_id: str) -> dict[str, Any]: ...

    def get_tasks(self, user_id: str, session_id: str) -> dict[str, Any]: ...

    def get_status(self, user_id: str, session_id: str, task_id: str) -> dict[str, Any]: ...


class WorkspaceService:
    """Build coherent UI snapshots from the stable public API."""

    def __init__(self, client: AgentClient) -> None:
        self.client = client

    def load_tasks(self, user_id: str, session_id: str) -> list[TaskView]:
        raw_items = self.client.get_tasks(user_id, session_id).get("task_ids") or []
        tasks: list[TaskView] = []
        for raw in raw_items:
            task_id, fallback_status = split_task_status(str(raw))
            payload = self.client.get_status(user_id, session_id, task_id)
            tasks.append(task_from_payload(task_id, fallback_status, payload))
        return sorted(tasks, key=lambda item: item.updated_at)

    def load_session(self, user_id: str, session_id: str) -> SessionView:
        tasks = self.load_tasks(user_id, session_id)
        updated_at = max((task.updated_at for task in tasks), default=0.0)
        return SessionView(
            session_id=session_id,
            title=session_title(tasks, session_id),
            updated_at=updated_at,
            tasks=tuple(tasks),
        )

    def list_sessions(
        self, user_id: str, *, include_session_id: str | None = None
    ) -> list[SessionView]:
        session_ids = [
            str(value) for value in self.client.get_sessions(user_id).get("session_ids") or []
        ]
        if include_session_id and include_session_id not in session_ids:
            session_ids.append(include_session_id)
        sessions = [self.load_session(user_id, session_id) for session_id in session_ids]
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def initialize(self, user_id: str) -> SessionView:
        active = str(self.client.get_active_session(user_id).get("active_session_id") or "")
        return (
            self.load_session(user_id, active)
            if active
            else self.load_session(user_id, str(uuid.uuid4()))
        )


def state_from_session(user_id: str, session: SessionView) -> dict[str, Any]:
    latest = session.tasks[-1] if session.tasks else None
    return {
        "user_id": user_id,
        "session_id": session.session_id,
        "task_id": latest.task_id if latest else None,
        "status": latest.status if latest else "idle",
        "messages": build_chat_messages(list(session.tasks)),
        "displayed_task_ids": [task.task_id for task in session.tasks],
        "persisted": bool(session.tasks),
        "poll_started": 0.0,
    }


def new_workspace_state(user_id: str, session_id: str | None = None) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "session_id": session_id or str(uuid.uuid4()),
        "task_id": None,
        "status": "idle",
        "messages": [],
        "displayed_task_ids": [],
        "persisted": False,
        "poll_started": 0.0,
    }
