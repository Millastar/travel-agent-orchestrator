"""Celery task entry points."""

from __future__ import annotations

import time
from typing import Any

from travel_agent_orchestrator.agent.runtime import execute_agent
from travel_agent_orchestrator.domain.models import SessionStatus
from travel_agent_orchestrator.infrastructure.config import get_settings
from travel_agent_orchestrator.infrastructure.logging import configure_logging
from travel_agent_orchestrator.infrastructure.redis_sessions import RedisSessionManager
from travel_agent_orchestrator.workers.celery_app import celery_app
from travel_agent_orchestrator.workers.event_loop import run_worker_coroutine


@celery_app.task
def invoke_agent_task(
    user_id: str,
    session_id: str,
    task_id: str,
    query: str,
    system_prompt: str,
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        settings = get_settings()
        configure_logging(settings)
        manager = RedisSessionManager.from_settings(settings)
        try:
            await manager.update_session(
                user_id,
                session_id,
                task_id,
                status=SessionStatus.RUNNING,
                last_query=query,
                last_updated=time.time(),
                ttl=settings.session_ttl_seconds,
            )
            return await execute_agent(
                settings=settings,
                session_manager=manager,
                user_id=user_id,
                session_id=session_id,
                task_id=task_id,
                query=query,
                system_prompt=system_prompt,
            )
        finally:
            await manager.close()

    return run_worker_coroutine(run())


@celery_app.task
def resume_agent_task(
    user_id: str,
    session_id: str,
    task_id: str,
    command_data: dict[str, Any],
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        settings = get_settings()
        configure_logging(settings)
        manager = RedisSessionManager.from_settings(settings)
        try:
            return await execute_agent(
                settings=settings,
                session_manager=manager,
                user_id=user_id,
                session_id=session_id,
                task_id=task_id,
                command_data=command_data,
            )
        finally:
            await manager.close()

    return run_worker_coroutine(run())
