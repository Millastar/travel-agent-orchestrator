"""Agent, memory, session, and task endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from travel_agent_orchestrator.api.dependencies import get_agent_service
from travel_agent_orchestrator.application.services import AgentService
from travel_agent_orchestrator.domain.models import (
    ActiveSessionInfoResponse,
    AgentRequest,
    InterruptResponse,
    LongMemRequest,
    SessionInfoResponse,
    SessionStatusResponse,
    TaskInfoResponse,
)

router = APIRouter(prefix="/agent", tags=["agent"])
Service = Annotated[AgentService, Depends(get_agent_service)]


@router.post("/invoke", response_model=dict, summary="Queue an agent task")
async def invoke_agent(request: AgentRequest, service: Service):
    return await service.invoke(request)


@router.post("/resume", response_model=dict, summary="Resume an interrupted agent task")
async def resume_agent(response: InterruptResponse, service: Service):
    return await service.resume(response)


@router.get(
    "/active/sessionid/{user_id}",
    response_model=ActiveSessionInfoResponse,
    summary="Get the most recently updated session",
)
async def get_agent_active_sessionid(user_id: str, service: Service):
    return await service.active_session(user_id)


@router.get(
    "/sessionids/{user_id}",
    response_model=SessionInfoResponse,
    summary="List a user's sessions",
)
async def get_agent_sessionids(user_id: str, service: Service):
    return await service.session_ids(user_id)


@router.get(
    "/tasks/{user_id}/{session_id}",
    response_model=TaskInfoResponse,
    summary="List tasks and statuses for a session",
)
async def get_agent_task_ids(user_id: str, session_id: str, service: Service):
    return await service.task_ids(user_id, session_id)


@router.get(
    "/status/{user_id}/{session_id}/{task_id}",
    response_model=SessionStatusResponse,
    summary="Get task execution state",
)
async def get_agent_status(user_id: str, session_id: str, task_id: str, service: Service):
    return await service.status(user_id, session_id, task_id)


@router.get("/trace/{user_id}/{session_id}/{task_id}", summary="Get a redacted execution trace")
async def get_agent_trace(user_id: str, session_id: str, task_id: str, service: Service):
    return await service.trace(user_id, session_id, task_id)


@router.post("/write/longterm", summary="Store a user's long-term preference")
async def write_long_term(request: LongMemRequest, service: Service):
    return await service.write_memory(request.user_id, request.memory_info)


@router.delete("/session/{user_id}/{session_id}", summary="Delete a session")
async def delete_agent_session(user_id: str, session_id: str, service: Service):
    return await service.delete_session(user_id, session_id)


@router.delete("/task/{user_id}/{session_id}/{task_id}", summary="Delete a task")
async def delete_agent_task(user_id: str, session_id: str, task_id: str, service: Service):
    return await service.delete_task(user_id, session_id, task_id)
