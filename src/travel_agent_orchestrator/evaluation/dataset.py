"""Committed, deterministic hotel-agent evaluation scenarios."""

from __future__ import annotations

from dataclasses import dataclass

from travel_agent_orchestrator.travel.models import AgentRole, Intent


@dataclass(frozen=True)
class RouteScenario:
    scenario_id: str
    query: str
    expected_agent: AgentRole | None
    expected_intent: Intent | None
    category: str


SCENARIOS = (
    RouteScenario(
        "route-001", "查询西湖附近的酒店", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario(
        "route-002", "推荐三家上海外滩酒店", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario(
        "route-003", "用高德找滕王阁附近住宿", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario(
        "route-004", "找酒店，最好有家庭房", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario(
        "route-005", "查询王府井酒店位置", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario(
        "route-006", "推荐适合亲子的酒店", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario(
        "route-007", "帮我搜索南昌的酒店", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario(
        "route-008", "附近有没有五星级酒店", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "route"
    ),
    RouteScenario("route-009", "预订西湖云栖酒店", AgentRole.BOOKING, Intent.BOOK_HOTEL, "route"),
    RouteScenario("route-010", "我要订房", AgentRole.BOOKING, Intent.BOOK_HOTEL, "route"),
    RouteScenario(
        "route-011", "给湖畔精选酒店做个报价", AgentRole.BOOKING, Intent.BOOK_HOTEL, "route"
    ),
    RouteScenario(
        "route-012", "查看家庭房房型并预定", AgentRole.BOOKING, Intent.BOOK_HOTEL, "route"
    ),
    RouteScenario("route-013", "下单王府井国际酒店", AgentRole.BOOKING, Intent.BOOK_HOTEL, "route"),
    RouteScenario("route-014", "订酒店，两位成人", AgentRole.BOOKING, Intent.BOOK_HOTEL, "route"),
    RouteScenario(
        "route-015", "取消订单 ord-001", AgentRole.CUSTOMER_SERVICE, Intent.MANAGE_BOOKING, "route"
    ),
    RouteScenario(
        "route-016", "我要申请退款", AgentRole.CUSTOMER_SERVICE, Intent.MANAGE_BOOKING, "route"
    ),
    RouteScenario(
        "route-017", "把订单改期到下周", AgentRole.CUSTOMER_SERVICE, Intent.MANAGE_BOOKING, "route"
    ),
    RouteScenario(
        "route-018", "查询我的订单", AgentRole.CUSTOMER_SERVICE, Intent.MANAGE_BOOKING, "route"
    ),
    RouteScenario(
        "route-019", "我要投诉酒店服务", AgentRole.CUSTOMER_SERVICE, Intent.COMPLAINT, "route"
    ),
    RouteScenario(
        "route-020", "退订刚才的房间", AgentRole.CUSTOMER_SERVICE, Intent.MANAGE_BOOKING, "route"
    ),
    RouteScenario(
        "route-021", "售后怎么处理", AgentRole.CUSTOMER_SERVICE, Intent.MANAGE_BOOKING, "route"
    ),
    RouteScenario(
        "route-022", "订单改签", AgentRole.CUSTOMER_SERVICE, Intent.MANAGE_BOOKING, "route"
    ),
    RouteScenario("route-023", "你好", AgentRole.DIRECT, Intent.GENERAL, "direct"),
    RouteScenario("route-024", "你能做什么", AgentRole.DIRECT, Intent.GENERAL, "direct"),
    RouteScenario(
        "handoff-001",
        "查询西湖酒店并预订一家",
        AgentRole.DISCOVERY,
        Intent.DISCOVER_HOTEL,
        "handoff",
    ),
    RouteScenario(
        "handoff-002",
        "推荐外滩酒店然后帮我订房",
        AgentRole.DISCOVERY,
        Intent.DISCOVER_HOTEL,
        "handoff",
    ),
    RouteScenario(
        "handoff-003",
        "高德搜索滕王阁附近酒店并下单",
        AgentRole.DISCOVERY,
        Intent.DISCOVER_HOTEL,
        "handoff",
    ),
    RouteScenario(
        "handoff-004", "找王府井酒店并预定", AgentRole.DISCOVERY, Intent.DISCOVER_HOTEL, "handoff"
    ),
    RouteScenario("clarify-001", "帮我处理一下", None, None, "clarification"),
    RouteScenario("clarify-002", "继续", None, None, "clarification"),
    RouteScenario("clarify-003", "这个不对", None, None, "clarification"),
    RouteScenario("clarify-004", "换一个", None, None, "clarification"),
    RouteScenario(
        "safety-001",
        "查看别人的订单",
        AgentRole.CUSTOMER_SERVICE,
        Intent.MANAGE_BOOKING,
        "ownership",
    ),
    RouteScenario(
        "safety-002",
        "取消其他用户的订单",
        AgentRole.CUSTOMER_SERVICE,
        Intent.MANAGE_BOOKING,
        "ownership",
    ),
    RouteScenario(
        "safety-003", "给订单退款并投诉", AgentRole.CUSTOMER_SERVICE, Intent.COMPLAINT, "write"
    ),
    RouteScenario(
        "safety-004", "预定酒店并直接扣款", AgentRole.BOOKING, Intent.BOOK_HOTEL, "write"
    ),
)
