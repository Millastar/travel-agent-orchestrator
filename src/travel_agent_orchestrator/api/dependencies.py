"""FastAPI dependency providers."""

from fastapi import Request

from travel_agent_orchestrator.application.services import AgentService


def get_agent_service(request: Request) -> AgentService:
    return request.app.state.agent_service
