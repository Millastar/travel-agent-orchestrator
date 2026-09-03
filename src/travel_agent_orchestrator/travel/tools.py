"""Role-scoped LangChain tools for discovery, booking, and after-sales."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.tools import BaseTool, tool

from travel_agent_orchestrator.agent.tools import get_agent_tools
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.travel.models import AgentRole, RiskLevel
from travel_agent_orchestrator.travel.service import HotelSandboxService


@dataclass(frozen=True)
class ToolPolicy:
    owner: AgentRole
    risk: RiskLevel
    description: str


HANDOFF_TOOL_TARGETS = {
    "handoff_to_discovery": AgentRole.DISCOVERY,
    "handoff_to_booking": AgentRole.BOOKING,
    "handoff_to_customer_service": AgentRole.CUSTOMER_SERVICE,
}


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str)


async def build_specialist_tools(
    settings: Settings,
    service: HotelSandboxService,
    *,
    user_id: str,
    task_id: str,
) -> tuple[dict[AgentRole, list[BaseTool]], dict[str, ToolPolicy]]:
    """Create closures that enforce the current user at the domain boundary."""

    @tool("search_sandbox_hotels")
    async def search_sandbox_hotels(city: str, keywords: str = "", limit: int = 5) -> str:
        """Search bookable sandbox hotels for recommendations and fallback discovery."""
        return _json(await service.search_hotels(city, keywords, min(max(limit, 1), 10)))

    @tool("prepare_hotel_quote")
    async def prepare_hotel_quote(
        hotel: str,
        check_in: date,
        check_out: date,
        guests: int = 1,
        room_type: str | None = None,
    ) -> str:
        """Create a 15-minute sandbox quote; this does not create an order or take payment."""
        quote = await service.create_quote(
            user_id=user_id,
            hotel=hotel,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
            room_type=room_type,
        )
        return quote.model_dump_json()

    @tool("confirm_hotel_booking")
    async def confirm_hotel_booking(quote_id: str, payment_method: str = "demo_wallet") -> str:
        """Confirm an existing quote and run the sandbox payment/booking saga exactly once."""
        order, saga = await service.confirm_booking(
            user_id=user_id,
            quote_id=quote_id,
            idempotency_key=f"{task_id}:{quote_id}",
            payment_method=payment_method,
        )
        return _json({"order": order.model_dump(mode="json"), "saga": saga})

    @tool("list_my_bookings")
    async def list_my_bookings() -> str:
        """List bookings owned by the current user; never returns another user's records."""
        return _json([item.model_dump(mode="json") for item in await service.list_orders(user_id)])

    @tool("get_booking")
    async def get_booking(order_id: str) -> str:
        """Read one current-user sandbox booking by order id."""
        return (await service.get_order(user_id, order_id)).model_dump_json()

    @tool("change_booking_dates")
    async def change_booking_dates(order_id: str, check_in: date, check_out: date) -> str:
        """Change dates on a confirmed sandbox order after human approval."""
        return (
            await service.change_dates(
                user_id=user_id,
                order_id=order_id,
                check_in=check_in,
                check_out=check_out,
            )
        ).model_dump_json()

    @tool("cancel_booking")
    async def cancel_booking(order_id: str, reason: str) -> str:
        """Cancel a confirmed sandbox order and apply its cancellation/refund rule."""
        return _json(
            await service.cancel_booking(user_id=user_id, order_id=order_id, reason=reason)
        )

    @tool("request_refund")
    async def request_refund(order_id: str, amount: float, reason: str) -> str:
        """Refund a captured sandbox payment after human approval."""
        return _json(
            await service.request_refund(
                user_id=user_id, order_id=order_id, amount=amount, reason=reason
            )
        )

    @tool("submit_complaint")
    async def submit_complaint(category: str, description: str, order_id: str | None = None) -> str:
        """Create a sandbox customer-service complaint after human approval."""
        return _json(
            await service.create_complaint(
                user_id=user_id,
                order_id=order_id,
                category=category,
                description=description,
            )
        )

    @tool("handoff_to_discovery")
    async def handoff_to_discovery(
        objective: str, constraints: dict[str, Any] | None = None
    ) -> str:
        """Transfer a task that needs hotel search or recommendation to Discovery Agent."""
        return _json({"objective": objective, "constraints": constraints or {}})

    @tool("handoff_to_booking")
    async def handoff_to_booking(
        objective: str,
        hotel: str | None = None,
        quote_id: str | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> str:
        """Transfer a selected hotel or quote to Booking Agent using structured fields."""
        return _json(
            {
                "objective": objective,
                "hotel": hotel,
                "quote_id": quote_id,
                "constraints": constraints or {},
            }
        )

    @tool("handoff_to_customer_service")
    async def handoff_to_customer_service(
        objective: str,
        order_id: str | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> str:
        """Transfer an existing-order or complaint task to Customer Service Agent."""
        return _json(
            {"objective": objective, "order_id": order_id, "constraints": constraints or {}}
        )

    amap_tools = await get_agent_tools(settings)
    discovery_tools = [
        *amap_tools,
        search_sandbox_hotels,
        handoff_to_booking,
    ]
    booking_tools = [
        prepare_hotel_quote,
        confirm_hotel_booking,
        get_booking,
        handoff_to_discovery,
        handoff_to_customer_service,
    ]
    service_tools = [
        list_my_bookings,
        get_booking,
        change_booking_dates,
        cancel_booking,
        request_refund,
        submit_complaint,
        handoff_to_booking,
        handoff_to_discovery,
    ]
    policies: dict[str, ToolPolicy] = {}
    for item in amap_tools:
        policies[item.name] = ToolPolicy(
            AgentRole.DISCOVERY, RiskLevel.READ_ONLY, "高德 MCP 只读地点查询"
        )
    for item, owner, risk, description in (
        (search_sandbox_hotels, AgentRole.DISCOVERY, RiskLevel.READ_ONLY, "沙箱酒店检索"),
        (prepare_hotel_quote, AgentRole.BOOKING, RiskLevel.READ_ONLY, "创建无交易副作用的报价"),
        (confirm_hotel_booking, AgentRole.BOOKING, RiskLevel.TRANSACTION, "创建订单并执行沙箱支付"),
        (list_my_bookings, AgentRole.CUSTOMER_SERVICE, RiskLevel.READ_ONLY, "查询本人订单"),
        (get_booking, AgentRole.CUSTOMER_SERVICE, RiskLevel.READ_ONLY, "查询本人订单详情"),
        (change_booking_dates, AgentRole.CUSTOMER_SERVICE, RiskLevel.TRANSACTION, "修改订单日期"),
        (cancel_booking, AgentRole.CUSTOMER_SERVICE, RiskLevel.DESTRUCTIVE, "取消订单并结算退款"),
        (request_refund, AgentRole.CUSTOMER_SERVICE, RiskLevel.DESTRUCTIVE, "执行沙箱退款"),
        (submit_complaint, AgentRole.CUSTOMER_SERVICE, RiskLevel.TRANSACTION, "创建投诉工单"),
    ):
        policies[item.name] = ToolPolicy(owner, risk, description)
    for name, target in HANDOFF_TOOL_TARGETS.items():
        policies[name] = ToolPolicy(target, RiskLevel.READ_ONLY, "结构化 Agent handoff")
    return {
        AgentRole.DISCOVERY: discovery_tools,
        AgentRole.BOOKING: booking_tools,
        AgentRole.CUSTOMER_SERVICE: service_tools,
    }, policies
