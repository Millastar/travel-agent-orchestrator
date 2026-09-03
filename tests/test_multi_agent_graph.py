from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from travel_agent_orchestrator.agent.graph import build_agent_graph
from travel_agent_orchestrator.agent.state import new_task_plan
from travel_agent_orchestrator.infrastructure.clock import RuntimeClock
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.travel.models import (
    AgentRole,
    Hotel,
    Intent,
    Quote,
    RoomOffer,
    RouteDecision,
)
from travel_agent_orchestrator.travel.repository import InMemoryTravelRepository
from travel_agent_orchestrator.travel.service import HotelSandboxService
from travel_agent_orchestrator.travel.tools import build_specialist_tools


class QueueModel:
    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])
        self.bound_tool_names: list[str] = []
        self.calls: list[list] = []

    def bind_tools(self, tools):
        self.bound_tool_names = [item.name for item in tools]
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if not self.responses:
            raise AssertionError("Unexpected model invocation")
        return self.responses.pop(0)


class LowConfidenceRouter:
    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return RouteDecision(
            intent=Intent.DISCOVER_HOTEL,
            target_agent=AgentRole.DISCOVERY,
            confidence=0.2,
            reason="请求信息不足",
        )


def _repository() -> InMemoryTravelRepository:
    hotel = Hotel(
        hotel_id="hotel-1",
        name="西湖云栖酒店",
        city="杭州",
        address="西湖区北山街 18 号",
        stars=5,
    )
    repository = InMemoryTravelRepository([hotel])
    room = RoomOffer(
        room_id="room-1",
        hotel_id=hotel.hotel_id,
        room_type="标准大床房",
        nightly_rate=688,
        capacity=2,
        available_rooms=8,
        cancellation_policy="入住前一天可免费取消。",
    )
    repository.rooms[room.room_id] = room
    return repository


def _state(query: str, task_id: str = "task-1") -> dict:
    return {
        "messages": [HumanMessage(content=query)],
        "system_prompt": "",
        "long_term_memory": "",
        "task_plan": new_task_plan(query),
        "pending_approval": None,
        "tool_call_queue": [],
        "approval_decision": None,
        "user_id": "user-1",
        "session_id": "session-1",
        "task_id": task_id,
        "trace_id": f"trace-{task_id}",
        "trace_sequence": 0,
        "active_agent": AgentRole.SUPERVISOR.value,
        "handoff_history": [],
        "travel_workflow": {"objective": query, "status": "received", "sandbox": True},
    }


def _build(models, repository, task_id="task-1", settings=None):
    settings = settings or Settings(app_env="test")
    service = HotelSandboxService(repository)
    tools, policies = asyncio.run(
        build_specialist_tools(settings, service, user_id="user-1", task_id=task_id)
    )
    graph = build_agent_graph(
        models=models,
        specialist_tools=tools,
        tool_policies=policies,
        checkpointer=MemorySaver(),
        store=InMemoryStore(),
        repository=repository,
        settings=settings,
        user_id="user-1",
        session_id="session-1",
    )
    return graph, tools, policies


def test_tools_are_isolated_by_specialist_role() -> None:
    repository = _repository()
    blank = {role: QueueModel() for role in AgentRole if role not in {AgentRole.DIRECT}}
    _, tools, policies = _build(blank, repository)

    discovery = {item.name for item in tools[AgentRole.DISCOVERY]}
    booking = {item.name for item in tools[AgentRole.BOOKING]}
    service = {item.name for item in tools[AgentRole.CUSTOMER_SERVICE]}

    assert "search_sandbox_hotels" in discovery
    assert "confirm_hotel_booking" not in discovery
    assert "confirm_hotel_booking" in booking
    assert "cancel_booking" not in booking
    assert "cancel_booking" in service
    assert policies["confirm_hotel_booking"].risk == "transaction"


def test_yearless_booking_dates_are_normalized_before_model_invocation(monkeypatch) -> None:
    fixed_clock = RuntimeClock(
        "Asia/Shanghai",
        datetime.fromisoformat("2026-08-31T09:00:00+08:00"),
    )
    monkeypatch.setattr(
        "travel_agent_orchestrator.agent.graph.runtime_clock",
        lambda _timezone: fixed_clock,
    )
    booking_model = QueueModel([AIMessage(content="报价日期已确认。")])
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: booking_model,
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, _repository(), task_id="date-normalization")

    result = asyncio.run(
        graph.ainvoke(
            _state("预订杭州黄龙店 9月10日到12日的双人房", "date-normalization"),
            config={"configurable": {"thread_id": "date-normalization"}},
        )
    )

    workflow = result["travel_workflow"]
    assert workflow["check_in"] == "2026-09-10"
    assert workflow["check_out"] == "2026-09-12"
    assert workflow["date_source"] == "server_normalized"
    prompt = "\n".join(str(message.content) for message in booking_model.calls[0])
    assert "当前日期：2026-08-31" in prompt
    assert '"check_in": "2026-09-10"' in prompt


def test_graph_rejects_a_specialist_tool_ownership_violation() -> None:
    repository = _repository()
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "confirm_hotel_booking",
                            "args": {"quote_id": "forged"},
                            "id": "forged-tool-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        AgentRole.BOOKING: QueueModel(),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)

    with pytest.raises(PermissionError, match="not available to discovery"):
        asyncio.run(
            graph.ainvoke(
                _state("查询西湖附近酒店"),
                config={"configurable": {"thread_id": "forged-tool"}},
            )
        )


def test_low_confidence_supervisor_requests_clarification() -> None:
    repository = _repository()
    models = {
        AgentRole.SUPERVISOR: LowConfidenceRouter(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: QueueModel(),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    result = asyncio.run(
        graph.ainvoke(
            _state("帮我安排一下"),
            config={"configurable": {"thread_id": "clarify"}},
        )
    )

    assert result["active_agent"] == AgentRole.DIRECT
    assert result["route_decision"]["intent"] == Intent.CLARIFY
    assert "说明是要搜索酒店" in result["final_response"]


def test_structured_handoff_switches_discovery_to_booking() -> None:
    repository = _repository()
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "handoff_to_booking",
                            "args": {
                                "objective": "预订西湖云栖酒店",
                                "hotel": "西湖云栖酒店",
                            },
                            "id": "handoff-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        AgentRole.BOOKING: QueueModel([AIMessage(content="请补充入住和离店日期。")]),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    result = asyncio.run(
        graph.ainvoke(
            _state("查询西湖酒店并预订一家"),
            config={"configurable": {"thread_id": "handoff"}},
        )
    )

    assert result["active_agent"] == AgentRole.BOOKING
    assert result["handoff_history"][0]["source"] == AgentRole.DISCOVERY
    assert result["handoff_history"][0]["target"] == AgentRole.BOOKING
    assert any(item["event_type"] == "agent.handoff" for item in repository.traces)


def test_string_handoff_constraints_are_normalized_instead_of_crashing() -> None:
    repository = _repository()
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel([AIMessage(content="我会按新的房型要求继续检索。")]),
        AgentRole.BOOKING: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "handoff_to_discovery",
                            "args": {
                                "objective": "重新搜索可预订房型",
                                "constraints": "更换标准间",
                            },
                            "id": "string-constraints",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)

    result = asyncio.run(
        graph.ainvoke(
            _state("预订酒店并更换标准间"),
            config={"configurable": {"thread_id": "string-handoff-constraints"}},
        )
    )

    assert result["active_agent"] == AgentRole.DISCOVERY
    assert result["handoff_history"][0]["constraints"] == {"request": "更换标准间"}
    assert result["final_response"] == "我会按新的房型要求继续检索。"


def test_more_results_routes_back_to_discovery_artifact_reader() -> None:
    repository = _repository()
    discovery_model = QueueModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_tool_artifact",
                        "args": {"artifact_id": "missing-artifact", "page": 2},
                        "id": "page-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="该结果已不存在，请重新搜索。"),
        ]
    )
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: discovery_model,
        AgentRole.BOOKING: QueueModel(),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    state = _state("查看更多")
    state["tool_artifact_refs"] = [{"artifact_id": "missing-artifact", "last_page": 1, "total": 10}]
    result = asyncio.run(
        graph.ainvoke(state, config={"configurable": {"thread_id": "artifact-more"}})
    )

    assert result["active_agent"] == AgentRole.DISCOVERY
    assert "read_tool_artifact" in discovery_model.bound_tool_names
    assert result["final_response"] == "该结果已不存在，请重新搜索。"


def test_duplicate_read_only_tool_call_is_stopped_and_answer_uses_existing_evidence() -> None:
    repository = _repository()
    repeated_call = {
        "name": "search_sandbox_hotels",
        "args": {"city": "杭州", "keywords": "酒店", "limit": 3},
        "id": "search-repeat",
        "type": "tool_call",
    }
    discovery_model = QueueModel(
        [
            AIMessage(content="", tool_calls=[{**repeated_call, "id": "search-first"}]),
            AIMessage(content="", tool_calls=[repeated_call]),
            AIMessage(content="基于已有结果，我推荐西湖云栖酒店。"),
        ]
    )
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: discovery_model,
        AgentRole.BOOKING: QueueModel(),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository, task_id="duplicate-read")

    result = asyncio.run(
        graph.ainvoke(
            _state("查询杭州酒店并推荐", "duplicate-read"),
            config={"configurable": {"thread_id": "duplicate-read"}},
        )
    )

    started = [item for item in repository.traces if item["event_type"] == "tool.started"]
    skipped = [item for item in repository.traces if item["event_type"] == "tool.skipped"]
    assert len(started) == 1
    assert skipped[0]["metadata"]["reason"] == "duplicate"
    assert result["tool_call_counts"] == {"discovery": 1}
    assert result["final_response"] == "基于已有结果，我推荐西湖云栖酒店。"


def test_discovery_budget_stops_search_and_still_allows_booking_handoff() -> None:
    repository = _repository()
    discovery_model = QueueModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_sandbox_hotels",
                        "args": {"city": "杭州", "keywords": "酒店", "limit": 3},
                        "id": "search-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_sandbox_hotels",
                        "args": {"city": "杭州", "keywords": "全季", "limit": 3},
                        "id": "search-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_sandbox_hotels",
                        "args": {"city": "杭州", "keywords": "评分", "limit": 3},
                        "id": "search-over-budget",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "handoff_to_booking",
                        "args": {
                            "objective": "为全季酒店生成演示报价",
                            "hotel": "西湖云栖酒店",
                            "constraints": {
                                "check_in": "2026-09-14",
                                "check_out": "2026-09-16",
                            },
                        },
                        "id": "handoff-after-budget",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: discovery_model,
        AgentRole.BOOKING: QueueModel([AIMessage(content="已进入报价阶段。")]),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    settings = Settings(app_env="test", max_read_only_tool_calls_per_agent=2)
    graph, _, _ = _build(models, repository, task_id="budget-handoff", settings=settings)

    result = asyncio.run(
        graph.ainvoke(
            _state("搜索酒店并生成报价", "budget-handoff"),
            config={
                "configurable": {"thread_id": "budget-handoff"},
                "recursion_limit": settings.graph_recursion_limit,
            },
        )
    )

    assert result["active_agent"] == AgentRole.BOOKING
    assert result["tool_call_counts"] == {"discovery": 2}
    assert result["travel_workflow"]["tool_budget_exhausted_agent"] == "discovery"
    assert result["final_response"] == "已进入报价阶段。"
    assert any(
        item["event_type"] == "tool.skipped"
        and item["metadata"]["reason"] == "budget_exhausted"
        for item in repository.traces
    )


def test_handoff_limit_stops_agent_ping_pong() -> None:
    repository = _repository()
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "handoff_to_booking",
                            "args": {"objective": "继续预订"},
                            "id": "handoff-over-limit",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        AgentRole.BOOKING: QueueModel(),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    state = _state("查询酒店并预订一家")
    state["handoff_history"] = [
        {"source": "discovery", "target": "booking", "reason": "loop"} for _ in range(6)
    ]
    result = asyncio.run(
        graph.ainvoke(state, config={"configurable": {"thread_id": "handoff-limit"}})
    )

    assert result["travel_workflow"]["status"] == "handoff_limit"
    assert "超过安全上限" in result["final_response"]


def test_transaction_tool_interrupts_and_accept_is_idempotent() -> None:
    repository = _repository()
    service = HotelSandboxService(repository)
    check_in = date.today() + timedelta(days=5)
    quote = asyncio.run(
        service.create_quote(
            user_id="user-1",
            hotel="hotel-1",
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
        )
    )
    booking_model = QueueModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "confirm_hotel_booking",
                        "args": {"quote_id": quote.quote_id, "payment_method": "demo_wallet"},
                        "id": "confirm-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: booking_model,
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    config = {"configurable": {"thread_id": "accept"}}
    interrupted = asyncio.run(graph.ainvoke(_state("预订西湖云栖酒店"), config=config))
    interrupt_data = interrupted["__interrupt__"][0].value

    assert interrupt_data["agent_role"] == AgentRole.BOOKING
    assert interrupt_data["risk_level"] == "transaction"
    result = asyncio.run(graph.ainvoke(Command(resume={"type": "accept"}), config=config))
    orders = asyncio.run(repository.list_orders("user-1"))
    assert len(orders) == 1
    assert orders[0].status == "confirmed"
    assert "模拟交易" in result["final_response"]


def test_rejecting_write_tool_does_not_create_order() -> None:
    repository = _repository()
    hotel = repository.hotels["hotel-1"]
    room = repository.rooms["room-1"]
    quote = Quote(
        quote_id="quote-1",
        user_id="user-1",
        hotel=hotel,
        room=room,
        check_in=date.today() + timedelta(days=2),
        check_out=date.today() + timedelta(days=3),
        guests=1,
        total_amount=688,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    repository.quotes[quote.quote_id] = quote
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "confirm_hotel_booking",
                            "args": {"quote_id": quote.quote_id},
                            "id": "confirm-2",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    config = {"configurable": {"thread_id": "reject"}}
    asyncio.run(graph.ainvoke(_state("预订西湖云栖酒店"), config=config))
    result = asyncio.run(graph.ainvoke(Command(resume={"type": "reject"}), config=config))

    assert asyncio.run(repository.list_orders("user-1")) == []
    assert "未创建或修改" in result["final_response"]


def test_edit_executes_only_the_replacement_quote() -> None:
    repository = _repository()
    service = HotelSandboxService(repository)
    check_in = date.today() + timedelta(days=5)
    original_quote = asyncio.run(
        service.create_quote(
            user_id="user-1",
            hotel="hotel-1",
            check_in=check_in,
            check_out=check_in + timedelta(days=1),
        )
    )
    replacement_quote = asyncio.run(
        service.create_quote(
            user_id="user-1",
            hotel="hotel-1",
            check_in=check_in + timedelta(days=2),
            check_out=check_in + timedelta(days=4),
        )
    )
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "confirm_hotel_booking",
                            "args": {"quote_id": original_quote.quote_id},
                            "id": "edit-confirm",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    config = {"configurable": {"thread_id": "edit"}}
    interrupted = asyncio.run(graph.ainvoke(_state("预订西湖云栖酒店"), config=config))
    assert (
        interrupted["__interrupt__"][0].value["action_request"]["args"]["quote_id"]
        == original_quote.quote_id
    )

    result = asyncio.run(
        graph.ainvoke(
            Command(
                resume={
                    "type": "edit",
                    "args": {
                        "args": {
                            "quote_id": replacement_quote.quote_id,
                            "payment_method": "demo_wallet",
                        }
                    },
                }
            ),
            config=config,
        )
    )

    orders = asyncio.run(repository.list_orders("user-1"))
    assert len(orders) == 1
    assert orders[0].quote_id == replacement_quote.quote_id
    assert orders[0].check_in == replacement_quote.check_in
    assert result["task_plan"]["constraints"]["quote_id"] == replacement_quote.quote_id
    assert any(item["event_type"] == "approval.edited" for item in repository.traces)


def test_feedback_returns_to_current_specialist_without_executing_write() -> None:
    repository = _repository()
    hotel = repository.hotels["hotel-1"]
    room = repository.rooms["room-1"]
    quote = Quote(
        quote_id="feedback-quote",
        user_id="user-1",
        hotel=hotel,
        room=room,
        check_in=date.today() + timedelta(days=2),
        check_out=date.today() + timedelta(days=3),
        guests=1,
        total_amount=688,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    repository.quotes[quote.quote_id] = quote
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "confirm_hotel_booking",
                            "args": {"quote_id": quote.quote_id},
                            "id": "feedback-confirm",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="已保留报价，但不会创建订单。"),
            ]
        ),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    config = {"configurable": {"thread_id": "feedback"}}
    asyncio.run(graph.ainvoke(_state("预订西湖云栖酒店"), config=config))
    result = asyncio.run(
        graph.ainvoke(
            Command(resume={"type": "response", "args": {"args": "先不要下单"}}),
            config=config,
        )
    )

    assert asyncio.run(repository.list_orders("user-1")) == []
    assert result["final_response"] == "已保留报价，但不会创建订单。"
    assert any(item["event_type"] == "approval.feedback" for item in repository.traces)


def test_multiple_write_calls_require_separate_approvals() -> None:
    repository = _repository()
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: QueueModel(),
        AgentRole.CUSTOMER_SERVICE: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "submit_complaint",
                            "args": {
                                "category": "服务",
                                "description": "前台服务等待时间过长",
                            },
                            "id": "complaint-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "submit_complaint",
                            "args": {
                                "category": "卫生",
                                "description": "房间卫生需要进一步检查",
                            },
                            "id": "complaint-2",
                            "type": "tool_call",
                        },
                    ],
                )
            ]
        ),
    }
    graph, _, _ = _build(models, repository)
    config = {"configurable": {"thread_id": "multi-approval"}}
    first = asyncio.run(graph.ainvoke(_state("投诉酒店服务和卫生"), config=config))
    assert first["__interrupt__"][0].value["action_request"]["action"] == "submit_complaint"

    second = asyncio.run(graph.ainvoke(Command(resume={"type": "accept"}), config=config))
    assert second["__interrupt__"][0].value["action_request"]["action"] == "submit_complaint"
    assert len(repository.complaints) == 1

    completed = asyncio.run(graph.ainvoke(Command(resume={"type": "accept"}), config=config))
    assert len(repository.complaints) == 2
    assert "投诉工单已创建" in completed["final_response"]
    assert sum(item["event_type"] == "approval.requested" for item in repository.traces) == 2


def test_terminal_write_failure_sets_explicit_error_state() -> None:
    repository = _repository()
    models = {
        AgentRole.SUPERVISOR: QueueModel(),
        AgentRole.DISCOVERY: QueueModel(),
        AgentRole.BOOKING: QueueModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "confirm_hotel_booking",
                            "args": {"quote_id": "missing-quote"},
                            "id": "missing-quote-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        AgentRole.CUSTOMER_SERVICE: QueueModel(),
    }
    graph, _, _ = _build(models, repository)
    config = {"configurable": {"thread_id": "tool-error"}}
    asyncio.run(graph.ainvoke(_state("预订西湖云栖酒店"), config=config))
    result = asyncio.run(graph.ainvoke(Command(resume={"type": "accept"}), config=config))

    assert result["execution_error"] == "DomainRuleError"
    assert "执行失败" in result["final_response"]
    completed_trace = next(
        item for item in repository.traces if item["event_type"] == "task.completed"
    )
    assert completed_trace["status"] == "error"
