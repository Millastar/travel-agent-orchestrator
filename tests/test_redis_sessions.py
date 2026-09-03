from __future__ import annotations

import asyncio

from tests.fakes import FakeRedis

from travel_agent_orchestrator.domain.models import AgentResponse
from travel_agent_orchestrator.infrastructure.redis_sessions import RedisSessionManager


def test_session_lifecycle_and_task_status_contract() -> None:
    async def scenario() -> None:
        fake = FakeRedis()
        manager = RedisSessionManager("redis://unused", 300, 3600, client=fake)

        await manager.create_session("user", "task", "session")
        assert await manager.user_id_exists("user") is True
        assert await manager.session_id_exists("user", "session") is True

        response = AgentResponse(session_id="session", task_id="task", status="completed")
        await manager.update_session(
            "user",
            "session",
            "task",
            status="completed",
            last_response=response,
        )
        await manager.set_task_status("task", "completed", user_id="user", session_id="session")

        assert await manager.inspect_runtime_targets() == [
            {"user_id": "user", "session_id": "session", "task_ids": ["task"]}
        ]

        session = await manager.get_session_by_task("user", "session", "task")
        assert session is not None
        assert session["last_response"].status == "completed"
        assert await manager.get_task_status("user", "session") == ["task:completed"]
        assert await manager.get_user_active_session_id("user") == "session"

        assert await manager.delete_session("user", "session", "task") is True
        assert await manager.session_task_id_exists("user", "session", "task") is False

    asyncio.run(scenario())
