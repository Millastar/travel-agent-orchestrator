"""LangGraph execution shared by Celery invoke and resume tasks."""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphRecursionError
from langgraph.store.postgres import AsyncPostgresStore
from langgraph.types import Command

from travel_agent_orchestrator.agent.context import ContextWindowExceeded
from travel_agent_orchestrator.agent.graph import build_agent_graph
from travel_agent_orchestrator.agent.memory import read_long_term_memory
from travel_agent_orchestrator.agent.results import (
    build_agent_response,
    filter_last_human_conversation,
)
from travel_agent_orchestrator.agent.state import new_task_plan
from travel_agent_orchestrator.domain.models import AgentResponse, SessionStatus
from travel_agent_orchestrator.infrastructure.clock import current_date
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.infrastructure.database import create_pool
from travel_agent_orchestrator.infrastructure.llm import get_chat_model
from travel_agent_orchestrator.infrastructure.redis_sessions import RedisSessionManager
from travel_agent_orchestrator.travel.models import AgentRole
from travel_agent_orchestrator.travel.repository import PostgresTravelRepository
from travel_agent_orchestrator.travel.service import HotelSandboxService
from travel_agent_orchestrator.travel.tools import build_specialist_tools

logger = logging.getLogger(__name__)


def conversation_thread_id(user_id: str, session_id: str) -> str:
    """Build a checkpoint key that cannot collide across application users."""
    return f"travel-agent-orchestrator:v1:{user_id}:{session_id}"


def graph_config(user_id: str, session_id: str) -> dict[str, dict[str, str]]:
    """Use the conversation identity for checkpoints while task ids track API work."""
    return {"configurable": {"thread_id": conversation_thread_id(user_id, session_id)}}


async def execute_agent(
    *,
    settings: Settings,
    session_manager: RedisSessionManager,
    user_id: str,
    session_id: str,
    task_id: str,
    query: str | None = None,
    system_prompt: str | None = None,
    command_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a new graph turn or resume a persisted HITL interruption."""
    pool = create_pool(settings)
    try:
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        store = AsyncPostgresStore(pool)
        repository = PostgresTravelRepository(pool)
        hotel_service = HotelSandboxService(
            repository,
            payment_mode=settings.sandbox_payment_mode,
            today_provider=lambda: current_date(settings.app_timezone),
        )
        models = {
            role: get_chat_model(settings, role.value)
            for role in (
                AgentRole.SUPERVISOR,
                AgentRole.DISCOVERY,
                AgentRole.BOOKING,
                AgentRole.CUSTOMER_SERVICE,
            )
        }
        specialist_tools, tool_policies = await build_specialist_tools(
            settings,
            hotel_service,
            user_id=user_id,
            task_id=task_id,
        )
        agent = build_agent_graph(
            models=models,
            specialist_tools=specialist_tools,
            tool_policies=tool_policies,
            checkpointer=checkpointer,
            store=store,
            repository=repository,
            settings=settings,
            user_id=user_id,
            session_id=session_id,
        )
        agent_config = graph_config(user_id, session_id)
        agent_config["recursion_limit"] = settings.graph_recursion_limit

        if command_data is not None:
            graph_input: dict[str, Any] | Command = Command(resume=command_data)
        else:
            memory = await read_long_term_memory(user_id, store)
            graph_input = {
                "messages": [{"role": "user", "content": query or ""}],
                "system_prompt": system_prompt or "",
                "long_term_memory": memory,
                "task_plan": new_task_plan(query or ""),
                "pending_approval": None,
                "tool_call_queue": [],
                "tool_call_history": [],
                "tool_call_counts": {},
                "approval_decision": None,
                "user_id": user_id,
                "session_id": session_id,
                "task_id": task_id,
                "trace_id": f"trace-{task_id}",
                "trace_sequence": 0,
                "active_agent": AgentRole.SUPERVISOR.value,
                "route_decision": {},
                "handoff_history": [],
                "travel_workflow": {
                    "objective": query or "",
                    "status": "received",
                    "sandbox": True,
                },
                "final_response": None,
                "execution_error": None,
            }

        result = await agent.ainvoke(
            graph_input,
            config=agent_config,
        )
        response = build_agent_response(session_id, task_id, result)
        await session_manager.update_session(
            user_id,
            session_id,
            task_id,
            status=response.status,
            last_response=response,
            last_updated=time.time(),
            ttl=settings.session_ttl_seconds,
        )
        await session_manager.set_task_status(
            task_id,
            response.status,
            result=filter_last_human_conversation(response),
            user_id=user_id,
            session_id=session_id,
        )
        logger.info(
            "Agent turn finished user=%s session=%s task=%s status=%s",
            user_id,
            session_id,
            task_id,
            response.status,
        )
        return response.model_dump(mode="json")
    except Exception as exc:
        logger.exception(
            "Agent turn failed user=%s session=%s task=%s", user_id, session_id, task_id
        )
        if isinstance(exc, ContextWindowExceeded):
            message = str(exc)
        elif isinstance(exc, GraphRecursionError):
            message = (
                "检索步骤超过安全上限，系统已停止继续调用工具。"
                "请缩小搜索条件，或稍后从当前会话继续。"
            )
        else:
            message = "智能体执行失败，请检查服务配置或稍后重试。"
        error_response = AgentResponse(
            session_id=session_id,
            task_id=task_id,
            status=SessionStatus.ERROR,
            message=message,
        )
        await session_manager.update_session(
            user_id,
            session_id,
            task_id,
            status=SessionStatus.ERROR,
            last_response=error_response,
            last_updated=time.time(),
            ttl=settings.session_ttl_seconds,
        )
        await session_manager.set_task_status(
            task_id,
            SessionStatus.ERROR,
            error=type(exc).__name__,
            user_id=user_id,
            session_id=session_id,
        )
        if isinstance(exc, ContextWindowExceeded | GraphRecursionError):
            return error_response.model_dump(mode="json")
        raise
    finally:
        await pool.close()
