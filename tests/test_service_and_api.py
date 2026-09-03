from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from tests.fakes import FakeMemoryStore, FakeRedis, FakeTask

from travel_agent_orchestrator.api.main import create_app
from travel_agent_orchestrator.application.services import AgentService
from travel_agent_orchestrator.domain.models import AgentResponse, SessionStatus
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.infrastructure.redis_sessions import RedisSessionManager
from travel_agent_orchestrator.travel.repository import InMemoryTravelRepository


def build_test_app() -> tuple[TestClient, FakeTask, FakeTask, FakeMemoryStore]:
    settings = Settings(app_env="test")
    invoke_task = FakeTask()
    resume_task = FakeTask()
    memory = FakeMemoryStore()
    sessions = RedisSessionManager("redis://unused", 300, 3600, client=FakeRedis())
    service = AgentService(
        settings,
        sessions,
        memory,
        invoke_task,
        resume_task,
        travel_repository=InMemoryTravelRepository(),
    )
    app = create_app(settings=settings, enable_lifespan=False)
    app.state.agent_service = service
    return TestClient(app), invoke_task, resume_task, memory


def test_existing_api_contracts_remain_available() -> None:
    client, invoke_task, _, memory = build_test_app()
    expected_paths = {
        "/agent/invoke",
        "/agent/resume",
        "/system/info",
        "/agent/active/sessionid/{user_id}",
        "/agent/sessionids/{user_id}",
        "/agent/tasks/{user_id}/{session_id}",
        "/agent/status/{user_id}/{session_id}/{task_id}",
        "/agent/write/longterm",
        "/agent/session/{user_id}/{session_id}",
        "/agent/task/{user_id}/{session_id}/{task_id}",
        "/agent/trace/{user_id}/{session_id}/{task_id}",
        "/travel/orders/{user_id}",
        "/travel/orders/{user_id}/{order_id}",
        "/system/metrics",
        "/evaluation/latest",
    }
    assert expected_paths <= {route.path for route in client.app.routes}

    payload = {
        "user_id": "user",
        "session_id": "session",
        "task_id": "task",
        "query": "hello",
    }

    response = client.post("/agent/invoke", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user",
        "session_id": "session",
        "task_id": "task",
    }
    assert invoke_task.calls[0]["query"] == "hello"

    status = client.get("/agent/status/user/session/task")
    assert status.status_code == 200
    assert status.json()["status"] == "idle"

    tasks = client.get("/agent/tasks/user/session")
    assert tasks.json() == {"task_ids": ["task:pending"]}

    memory_response = client.post(
        "/agent/write/longterm",
        json={"user_id": "user", "memory_info": "prefer concise replies"},
    )
    assert memory_response.status_code == 200
    assert memory_response.json()["status"] == "success"
    assert memory.items[0]["namespace"] == ("memories", "user")

    system = client.get("/system/info")
    assert system.status_code == 200
    assert system.json()["sessions_count"] == 1

    assert client.get("/agent/trace/user/session/task").json() == {
        "task_id": "task",
        "events": [],
    }
    assert client.get("/travel/orders/user").json() == {"orders": []}
    assert client.get("/system/metrics").json()["tasks"] == 0
    assert client.get("/evaluation/latest").status_code == 200


def test_gradio_workspace_is_mounted_without_hiding_api_docs() -> None:
    client, _, _, _ = build_test_app()

    page = client.get("/")
    docs = client.get("/docs")

    assert page.status_code == 200
    assert "Travel Agent Orchestrator" in page.text
    assert docs.status_code == 200


def test_resume_rejects_missing_and_non_interrupted_tasks() -> None:
    client, _, resume_task, _ = build_test_app()
    resume = {
        "user_id": "user",
        "session_id": "session",
        "task_id": "task",
        "response_type": "accept",
    }
    assert client.post("/agent/resume", json=resume).status_code == 404

    client.post(
        "/agent/invoke",
        json={**resume, "query": "hello"},
    )
    assert client.post("/agent/resume", json=resume).status_code == 400
    assert resume_task.calls == []


def test_resume_passes_interrupted_tool_name_to_worker_without_changing_api() -> None:
    client, _, resume_task, _ = build_test_app()
    request = {
        "user_id": "user",
        "session_id": "session",
        "task_id": "task",
        "query": "查询酒店",
    }
    client.post("/agent/invoke", json=request)
    service = client.app.state.agent_service
    interrupted = AgentResponse(
        session_id="session",
        task_id="task",
        status=SessionStatus.INTERRUPTED,
        interrupt_data={
            "action_request": {
                "action": "maps_text_search",
                "args": {"keywords": "滕王阁附近酒店"},
            }
        },
    )
    asyncio.run(
        service.sessions.update_session(
            "user",
            "session",
            "task",
            status=SessionStatus.INTERRUPTED,
            last_response=interrupted,
        )
    )

    response = client.post(
        "/agent/resume",
        json={
            "user_id": "user",
            "session_id": "session",
            "task_id": "task",
            "response_type": "edit",
            "args": {"args": {"keywords": "滕王阁附近旅馆"}},
        },
    )

    assert response.status_code == 200
    assert resume_task.calls[0]["command_data"]["_tool_name"] == "maps_text_search"


def test_delete_contract_uses_professional_message() -> None:
    client, _, _, _ = build_test_app()
    client.post(
        "/agent/invoke",
        json={
            "user_id": "user",
            "session_id": "session",
            "task_id": "task",
            "query": "hello",
        },
    )

    response = client.delete("/agent/session/user/session")

    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "会话 session 已删除。"}


def test_delete_session_also_removes_conversation_checkpoint() -> None:
    class FakeCheckpointSaver:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

    settings = Settings(app_env="test")
    sessions = RedisSessionManager("redis://unused", 300, 3600, client=FakeRedis())
    checkpoint_saver = FakeCheckpointSaver()
    memory = FakeMemoryStore()
    asyncio.run(
        memory.aput(
            namespace=("memories", "user"),
            key="memory-1",
            value={"data": "偏好安静"},
        )
    )
    asyncio.run(
        memory.aput(
            namespace=("tool_artifacts", "user", "session"),
            key="artifact-1",
            value={"raw_output": {"pois": []}},
        )
    )
    service = AgentService(
        settings,
        sessions,
        memory,
        FakeTask(),
        FakeTask(),
        checkpoint_saver,
    )
    asyncio.run(
        sessions.create_session(
            user_id="user",
            session_id="session",
            task_id="task",
            status=SessionStatus.IDLE,
        )
    )

    asyncio.run(service.delete_session("user", "session"))

    assert checkpoint_saver.deleted == ["travel-agent-orchestrator:v1:user:session"]
    assert len(memory.items) == 1
    assert memory.items[0]["namespace"] == ("memories", "user")
