"""Hotel sandbox domain used by the specialist agents."""

from travel_agent_orchestrator.travel.models import (
    AgentRole,
    HandoffEnvelope,
    Intent,
    OrderStatus,
    RiskLevel,
    RouteDecision,
)

__all__ = [
    "AgentRole",
    "HandoffEnvelope",
    "Intent",
    "OrderStatus",
    "RiskLevel",
    "RouteDecision",
]
