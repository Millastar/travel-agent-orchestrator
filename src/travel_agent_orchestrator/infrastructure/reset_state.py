"""Targeted reset for legacy runtime sessions without deleting long-term memory."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from travel_agent_orchestrator.agent.artifacts import artifact_namespace
from travel_agent_orchestrator.agent.runtime import conversation_thread_id
from travel_agent_orchestrator.infrastructure.config import Settings, get_settings
from travel_agent_orchestrator.infrastructure.database import create_pool
from travel_agent_orchestrator.infrastructure.redis_sessions import RedisSessionManager


async def _delete_artifacts(store: Any, user_id: str, session_id: str) -> int:
    namespace = artifact_namespace(user_id, session_id)
    deleted = 0
    while True:
        items = await store.asearch(namespace, query="", limit=100, offset=0)
        if not items:
            return deleted
        for item in items:
            await store.adelete(namespace, str(item.key))
            deleted += 1


async def reset_runtime_state(settings: Settings, *, confirm: bool) -> dict[str, Any]:
    sessions = RedisSessionManager.from_settings(settings)
    pool = None
    try:
        targets = await sessions.inspect_runtime_targets()
        report: dict[str, Any] = {
            "mode": "confirmed" if confirm else "dry-run",
            "sessions": len(targets),
            "tasks": sum(len(item["task_ids"]) for item in targets),
            "long_term_memory_preserved": True,
        }
        if not confirm:
            return report

        pool = create_pool(settings)
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        store = AsyncPostgresStore(pool)
        deleted_artifacts = 0
        deleted_threads: set[str] = set()
        for target in targets:
            user_id = str(target["user_id"])
            session_id = str(target["session_id"])
            thread_ids = {
                conversation_thread_id(user_id, session_id),
                f"{user_id}:{session_id}",
                *[str(task_id) for task_id in target["task_ids"]],
            }
            for thread_id in thread_ids:
                await checkpointer.adelete_thread(thread_id)
                deleted_threads.add(thread_id)
            deleted_artifacts += await _delete_artifacts(store, user_id, session_id)
            await sessions.delete_session(user_id, session_id)
        report.update(
            {
                "checkpoint_threads_deleted": len(deleted_threads),
                "tool_artifacts_deleted": deleted_artifacts,
            }
        )
        return report
    finally:
        await sessions.close()
        if pool is not None:
            await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or clear project runtime sessions while preserving long-term memory."
    )
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="Apply the targeted reset. Omit this flag for a dry run.",
    )
    arguments = parser.parse_args()
    report = asyncio.run(reset_runtime_state(get_settings(), confirm=arguments.confirm_reset))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
