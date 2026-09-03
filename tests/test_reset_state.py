from __future__ import annotations

import asyncio

import travel_agent_orchestrator.infrastructure.reset_state as reset_module
from travel_agent_orchestrator.infrastructure.config import Settings


class FakeSessions:
    def __init__(self) -> None:
        self.closed = False

    async def inspect_runtime_targets(self):
        return [
            {
                "user_id": "user",
                "session_id": session_id,
                "task_ids": [f"task-{session_id}"],
            }
            for session_id in ("session-a", "session-b")
        ]

    async def close(self) -> None:
        self.closed = True


def test_reset_is_dry_run_by_default_and_preserves_long_term_memory(monkeypatch) -> None:
    sessions = FakeSessions()
    monkeypatch.setattr(
        reset_module.RedisSessionManager,
        "from_settings",
        lambda _settings: sessions,
    )

    report = asyncio.run(reset_module.reset_runtime_state(Settings(app_env="test"), confirm=False))

    assert report == {
        "mode": "dry-run",
        "sessions": 2,
        "tasks": 2,
        "long_term_memory_preserved": True,
    }
    assert sessions.closed is True
