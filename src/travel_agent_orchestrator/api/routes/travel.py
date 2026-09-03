"""Read-only hotel sandbox endpoints used by the demo workspace."""

from typing import Annotated

from fastapi import APIRouter, Depends

from travel_agent_orchestrator.api.dependencies import get_agent_service
from travel_agent_orchestrator.application.services import AgentService

router = APIRouter(prefix="/travel", tags=["travel"])
Service = Annotated[AgentService, Depends(get_agent_service)]


@router.get("/orders/{user_id}", summary="List current-user sandbox orders")
async def list_orders(user_id: str, service: Service):
    return await service.orders(user_id)


@router.get("/orders/{user_id}/{order_id}", summary="Get one current-user sandbox order")
async def get_order(user_id: str, order_id: str, service: Service):
    return await service.order(user_id, order_id)
