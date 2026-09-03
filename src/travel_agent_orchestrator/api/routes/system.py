"""System information endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends

from travel_agent_orchestrator.api.dependencies import get_agent_service
from travel_agent_orchestrator.application.services import AgentService
from travel_agent_orchestrator.domain.models import SystemInfoResponse

router = APIRouter(prefix="/system", tags=["system"])
Service = Annotated[AgentService, Depends(get_agent_service)]


@router.get("/info", response_model=SystemInfoResponse, summary="Get session statistics")
async def get_system_info(service: Service):
    return await service.system_info()


@router.get("/metrics", response_model=dict, summary="Get local Multi-Agent metrics")
async def get_system_metrics(service: Service):
    return await service.metrics()
