"""FastAPI application factory and resource lifecycle."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from travel_agent_orchestrator.api.routes import agent, evaluation, system, travel
from travel_agent_orchestrator.application.services import AgentService
from travel_agent_orchestrator.infrastructure.config import Settings, get_settings
from travel_agent_orchestrator.infrastructure.database import create_pool
from travel_agent_orchestrator.infrastructure.logging import configure_logging
from travel_agent_orchestrator.infrastructure.redis_sessions import RedisSessionManager
from travel_agent_orchestrator.travel.repository import PostgresTravelRepository

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    enable_lifespan: bool = True,
    enable_web: bool = True,
) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved_settings)
        sessions: RedisSessionManager | None = None
        pool = None
        try:
            sessions = RedisSessionManager.from_settings(resolved_settings)
            await sessions.ping()
            pool = create_pool(resolved_settings)
            await pool.open()
            store = AsyncPostgresStore(pool)
            checkpoint_saver = AsyncPostgresSaver(pool)
            # Worker imports are intentionally lazy so API schemas and tests do not
            # initialize optional MCP transports before application startup.
            from travel_agent_orchestrator.workers.tasks import invoke_agent_task, resume_agent_task

            app.state.agent_service = AgentService(
                resolved_settings,
                sessions,
                store,
                invoke_agent_task,
                resume_agent_task,
                checkpoint_saver,
                PostgresTravelRepository(pool),
            )
            logger.info("API resources initialized")
            yield
        except Exception:
            logger.exception("API startup failed")
            raise
        finally:
            if sessions is not None:
                await sessions.close()
            if pool is not None:
                await pool.close()
            logger.info("API resources closed")

    kwargs = {"lifespan": lifespan} if enable_lifespan else {}
    app = FastAPI(
        title="Travel Agent Orchestrator",
        version="2.0.0",
        description=(
            "Hotel Multi-Agent platform with role-isolated tools, sandbox transactions, "
            "persistent traces, MCP discovery, and human approval."
        ),
        **kwargs,
    )
    app.include_router(system.router)
    app.include_router(agent.router)
    app.include_router(travel.router)
    app.include_router(evaluation.router)
    if enable_web:
        import gradio as gr

        from travel_agent_orchestrator.web import build_web_demo

        demo = build_web_demo(resolved_settings)
        app = gr.mount_gradio_app(
            app,
            demo,
            path="/",
            server_name=resolved_settings.host,
            server_port=resolved_settings.port,
            show_api=False,
            show_error=resolved_settings.debug,
            enable_monitoring=False,
        )
    return app


app = create_app()
