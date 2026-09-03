from __future__ import annotations

from typing import Any

from travel_agent_orchestrator.web.presentation import INTERRUPT_PROMPT
from travel_agent_orchestrator.web.service import (
    WorkspaceService,
    new_workspace_state,
    state_from_session,
)


class FakeAgentClient:
    def __init__(self) -> None:
        self.payloads = {
            ("older", "task-a"): {
                "status": "completed",
                "last_query": "较早问题",
                "last_updated": 10,
                "last_response": {"result": {"messages": [{"content": "较早回答"}]}},
            },
            ("newer", "task-b"): {
                "status": "interrupted",
                "last_query": "预订酒店",
                "last_updated": 20,
                "last_response": {
                    "interrupt_data": {
                        "action_request": {"action": "confirm_hotel_booking", "args": {}}
                    }
                },
            },
        }

    def get_active_session(self, _: str) -> dict[str, Any]:
        return {"active_session_id": "newer"}

    def get_sessions(self, _: str) -> dict[str, Any]:
        return {"session_ids": ["older", "newer"]}

    def get_tasks(self, _: str, session_id: str) -> dict[str, Any]:
        task_id = "task-a" if session_id == "older" else "task-b"
        return {"task_ids": [f"{task_id}:pending"]}

    def get_status(self, _: str, session_id: str, task_id: str) -> dict[str, Any]:
        return self.payloads[(session_id, task_id)]


def test_sessions_are_sorted_by_real_update_time_and_restore_interrupt() -> None:
    service = WorkspaceService(FakeAgentClient())

    sessions = service.list_sessions("user")
    state = state_from_session("user", service.initialize("user"))

    assert [session.session_id for session in sessions] == ["newer", "older"]
    assert state["session_id"] == "newer"
    assert state["task_id"] == "task-b"
    assert state["status"] == "interrupted"
    assert state["messages"] == [
        {"role": "user", "content": "预订酒店"},
        {"role": "assistant", "content": INTERRUPT_PROMPT},
    ]


def test_new_workspace_is_ready_for_a_fresh_question() -> None:
    state = new_workspace_state("demo", "session")

    assert state == {
        "user_id": "demo",
        "session_id": "session",
        "task_id": None,
        "status": "idle",
        "messages": [],
        "displayed_task_ids": [],
        "persisted": False,
        "poll_started": 0.0,
    }
