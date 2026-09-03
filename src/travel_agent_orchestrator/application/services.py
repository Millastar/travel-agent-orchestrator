"""Use-case services for API routes."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Protocol

from fastapi import HTTPException

from travel_agent_orchestrator.agent.artifacts import artifact_namespace
from travel_agent_orchestrator.agent.memory import memory_namespace
from travel_agent_orchestrator.agent.runtime import conversation_thread_id
from travel_agent_orchestrator.domain.models import (
    ActiveSessionInfoResponse,
    AgentRequest,
    InterruptDecision,
    InterruptResponse,
    SessionInfoResponse,
    SessionStatus,
    SessionStatusResponse,
    SystemInfoResponse,
    TaskInfoResponse,
)
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.infrastructure.redis_sessions import RedisSessionManager

logger = logging.getLogger(__name__)


class AsyncTask(Protocol):
    def delay(self, **kwargs: Any) -> Any: ...


class AgentService:
    """Coordinate API validation, persistence, and background task dispatch."""

    def __init__(
        self,
        settings: Settings,
        sessions: RedisSessionManager,
        memory_store: Any,
        invoke_task: AsyncTask,
        resume_task: AsyncTask,
        checkpoint_saver: Any | None = None,
        travel_repository: Any | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.memory_store = memory_store
        self.invoke_task = invoke_task
        self.resume_task = resume_task
        self.checkpoint_saver = checkpoint_saver
        self.travel_repository = travel_repository

    async def invoke(self, request: AgentRequest) -> dict[str, str]:
        exists = await self.sessions.session_task_id_exists(
            request.user_id, request.session_id, request.task_id
        )
        if not exists:
            await self.sessions.create_session(
                user_id=request.user_id,
                session_id=request.session_id,
                task_id=request.task_id,
                status=SessionStatus.IDLE,
                last_updated=time.time(),
                ttl=self.settings.session_ttl_seconds,
            )
        self.invoke_task.delay(
            user_id=request.user_id,
            session_id=request.session_id,
            task_id=request.task_id,
            query=request.query,
            system_prompt=request.system_message or "",
        )
        await self.sessions.set_task_status(
            request.task_id,
            SessionStatus.PENDING,
            user_id=request.user_id,
            session_id=request.session_id,
        )
        logger.info(
            "Queued agent task user=%s session=%s task=%s",
            request.user_id,
            request.session_id,
            request.task_id,
        )
        return {
            "user_id": request.user_id,
            "session_id": request.session_id,
            "task_id": request.task_id,
        }

    async def resume(self, request: InterruptResponse) -> dict[str, str]:
        if request.response_type not in {decision.value for decision in InterruptDecision}:
            raise HTTPException(status_code=400, detail="不支持的人工审批操作。")
        session = await self.sessions.get_session_by_task(
            request.user_id, request.session_id, request.task_id
        )
        if not session:
            raise HTTPException(status_code=404, detail="未找到指定的会话任务。")
        if session.get("status") != SessionStatus.INTERRUPTED:
            raise HTTPException(status_code=400, detail="只有已中断的任务可以恢复。")

        await self.sessions.update_session(
            request.user_id,
            request.session_id,
            request.task_id,
            status=SessionStatus.RUNNING,
            last_updated=time.time(),
            ttl=self.settings.session_ttl_seconds,
            clear_last_response=True,
        )
        command_data: dict[str, Any] = {"type": request.response_type}
        if request.args is not None:
            command_data["args"] = request.args
        last_response = session.get("last_response")
        interrupt_data = getattr(last_response, "interrupt_data", None)
        if isinstance(last_response, dict):
            interrupt_data = last_response.get("interrupt_data")
        action_request = (
            interrupt_data.get("action_request") if isinstance(interrupt_data, dict) else None
        )
        if isinstance(action_request, dict) and action_request.get("action"):
            command_data["_tool_name"] = str(action_request["action"])
        self.resume_task.delay(
            user_id=request.user_id,
            session_id=request.session_id,
            task_id=request.task_id,
            command_data=command_data,
        )
        await self.sessions.set_task_status(
            request.task_id,
            SessionStatus.PENDING,
            user_id=request.user_id,
            session_id=request.session_id,
        )
        return {
            "user_id": request.user_id,
            "session_id": request.session_id,
            "task_id": request.task_id,
        }

    async def system_info(self) -> SystemInfoResponse:
        return SystemInfoResponse(
            sessions_count=await self.sessions.get_session_count(),
            active_users=await self.sessions.get_all_users_session_ids(),
        )

    async def trace(self, user_id: str, session_id: str, task_id: str) -> dict[str, Any]:
        if self.travel_repository is None:
            return {"task_id": task_id, "events": []}
        return {
            "task_id": task_id,
            "events": await self.travel_repository.get_trace(user_id, session_id, task_id),
        }

    async def orders(self, user_id: str) -> dict[str, Any]:
        if self.travel_repository is None:
            return {"orders": []}
        orders = await self.travel_repository.list_orders(user_id)
        return {"orders": [item.model_dump(mode="json") for item in orders]}

    async def order(self, user_id: str, order_id: str) -> dict[str, Any]:
        if self.travel_repository is None:
            raise HTTPException(status_code=404, detail="未找到指定的沙箱订单。")
        order = await self.travel_repository.get_order(user_id, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="未找到指定的沙箱订单。")
        return order.model_dump(mode="json")

    async def metrics(self) -> dict[str, Any]:
        if self.travel_repository is None:
            return {
                "tasks": 0,
                "completed": 0,
                "errors": 0,
                "success_rate": 0,
                "route_distribution": {},
                "agent_latency_ms": {},
                "tool_latency_ms": {},
                "failure_types": {},
                "compensation_results": {"attempted": 0, "succeeded": 0},
            }
        return await self.travel_repository.get_metrics()

    async def active_session(self, user_id: str) -> ActiveSessionInfoResponse:
        if not await self.sessions.user_id_exists(user_id):
            return ActiveSessionInfoResponse(active_session_id="")
        session_id = await self.sessions.get_user_active_session_id(user_id)
        return ActiveSessionInfoResponse(active_session_id=session_id or "")

    async def session_ids(self, user_id: str) -> SessionInfoResponse:
        if not await self.sessions.user_id_exists(user_id):
            return SessionInfoResponse(session_ids=[])
        return SessionInfoResponse(session_ids=await self.sessions.get_all_session_ids(user_id))

    async def task_ids(self, user_id: str, session_id: str) -> TaskInfoResponse:
        if not await self.sessions.session_id_exists(user_id, session_id):
            return TaskInfoResponse(task_ids=[])
        return TaskInfoResponse(task_ids=await self.sessions.get_task_status(user_id, session_id))

    async def status(self, user_id: str, session_id: str, task_id: str) -> SessionStatusResponse:
        session = await self.sessions.get_session_by_task(user_id, session_id, task_id)
        if not session:
            return SessionStatusResponse(
                user_id=user_id,
                session_id=session_id,
                task_id=task_id,
                status=SessionStatus.NOT_FOUND,
                message="未找到指定的会话任务。",
            )
        return SessionStatusResponse(
            user_id=user_id,
            session_id=session_id,
            task_id=task_id,
            status=session.get("status", SessionStatus.ERROR),
            last_query=session.get("last_query"),
            last_updated=session.get("last_updated"),
            last_response=session.get("last_response"),
        )

    async def write_memory(self, user_id: str, memory_info: str) -> dict[str, str]:
        if not await self.sessions.user_id_exists(user_id):
            raise HTTPException(status_code=404, detail="当前用户尚未建立会话。")
        memory_id = str(uuid.uuid4())
        try:
            await self.memory_store.aput(
                namespace=memory_namespace(user_id),
                key=memory_id,
                value={"data": memory_info},
            )
        except Exception as exc:
            logger.exception("Long-term memory write failed user=%s", user_id)
            raise HTTPException(status_code=500, detail="长期记忆保存失败，请稍后重试。") from exc
        logger.info("Stored long-term memory user=%s memory=%s", user_id, memory_id)
        return {"status": "success", "memory_id": memory_id, "message": "长期记忆已保存。"}

    async def delete_session(self, user_id: str, session_id: str) -> dict[str, str]:
        if not await self.sessions.session_id_exists(user_id, session_id):
            raise HTTPException(status_code=404, detail="未找到指定的会话。")
        if self.checkpoint_saver is not None:
            await self.checkpoint_saver.adelete_thread(conversation_thread_id(user_id, session_id))
        namespace = artifact_namespace(user_id, session_id)
        while True:
            artifacts = await self.memory_store.asearch(
                namespace,
                query="",
                limit=100,
                offset=0,
            )
            if not artifacts:
                break
            for artifact in artifacts:
                await self.memory_store.adelete(namespace, str(artifact.key))
            if len(artifacts) < 100:
                break
        await self.sessions.delete_session(user_id, session_id)
        return {"status": "success", "message": f"会话 {session_id} 已删除。"}

    async def delete_task(self, user_id: str, session_id: str, task_id: str) -> dict[str, str]:
        if not await self.sessions.session_task_id_exists(user_id, session_id, task_id):
            raise HTTPException(status_code=404, detail="未找到指定的任务。")
        await self.sessions.delete_session(user_id, session_id, task_id)
        return {"status": "success", "message": f"任务 {task_id} 已删除。"}
