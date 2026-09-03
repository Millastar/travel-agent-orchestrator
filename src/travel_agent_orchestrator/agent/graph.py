"""Custom hotel Multi-Agent graph with routing, handoffs, HITL, and traces."""

from __future__ import annotations

import json
import logging
import time
from contextlib import suppress
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from travel_agent_orchestrator.agent.artifacts import persist_tool_result, read_artifact_page
from travel_agent_orchestrator.agent.context import (
    ContextWindowExceeded,
    build_model_input,
    compress_context,
    estimate_context_tokens,
)
from travel_agent_orchestrator.agent.state import (
    AgentState,
    task_state_for_public,
    update_task_plan,
)
from travel_agent_orchestrator.domain.models import InterruptDecision
from travel_agent_orchestrator.infrastructure.clock import resolve_date_range, runtime_clock
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.observability import record_trace
from travel_agent_orchestrator.travel.models import (
    AgentRole,
    HandoffEnvelope,
    Intent,
    RiskLevel,
    RouteDecision,
)
from travel_agent_orchestrator.travel.repository import TravelRepository
from travel_agent_orchestrator.travel.tools import HANDOFF_TOOL_TARGETS, ToolPolicy

logger = logging.getLogger(__name__)
ARTIFACT_READER_NAME = "read_tool_artifact"

ROLE_PROMPTS = {
    AgentRole.DISCOVERY: """
你是 Discovery Agent，只负责酒店地点检索和个性化推荐，只能使用分配给你的只读工具。
优先使用高德 MCP 获取实时地点；不可用时使用沙箱目录。不得声称地点结果已经产生真实预订。
若用户同时要求预订：选项已经明确时调用 handoff_to_booking；不明确时先给出候选并询问用户。
用户要求“查看更多”或“下一页”时，使用最近的 artifact_id，并将 artifact_page 加一。
高德结果不保证提供评分；缺少评分时应明确说明，并依据距离、地址和交通线索完成推荐，
不得为了缺失字段反复执行相同或近似搜索。需要地点详情时，用 poi_id 或名称读取一次产物详情。
用户已指定优先酒店且要求报价时，在确认候选后立即结构化交接给 Booking Agent。
""",
    AgentRole.BOOKING: """
你是 Booking Agent，只负责沙箱房型、报价和预订。不得处理投诉或自由调用搜索工具。
入住日期、离店日期、人数或酒店缺失时必须询问，不得编造。先调用 prepare_hotel_quote，
向用户说明报价后再调用 confirm_hotel_booking；确认工具会触发一次包含支付信息的人工审批。
所有订单都是模拟交易，回答必须明确“沙箱订单”。
""",
    AgentRole.CUSTOMER_SERVICE: """
你是 Customer Service Agent，只处理当前用户的订单查询、改期、取消、退款和投诉。
先查询订单再执行写操作；订单号缺失时使用 list_my_bookings。写操作会进入人工审批。
不得访问其他用户的数据，不得承诺沙箱规则之外的赔付。
""",
}


@tool(ARTIFACT_READER_NAME)
async def artifact_reader_tool(
    artifact_id: str,
    page: int = 1,
    poi_id: str | None = None,
    name: str | None = None,
) -> str:
    """Read a safe POI detail by id/name, or a page owned by this conversation."""
    raise RuntimeError("Artifact reads are executed by the graph")


def _latest_human_text(state: AgentState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content)
        if isinstance(message, dict) and message.get("role") in {"user", "human"}:
            return str(message.get("content") or "")
    return str((state.get("travel_workflow") or {}).get("objective") or "")


def _latest_ai(state: AgentState) -> AIMessage | None:
    return next(
        (item for item in reversed(state.get("messages") or []) if isinstance(item, AIMessage)),
        None,
    )


def _coerce_handoff_constraints(value: Any) -> dict[str, Any]:
    """Normalize imperfect model output without allowing it to crash graph execution."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        return {"request": value.strip()}
    if isinstance(value, list):
        return {"items": value}
    return {}


def _tool_fingerprint(role: AgentRole, name: str, arguments: dict[str, Any]) -> str:
    normalized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return f"{role.value}:{name}:{normalized}"


def _rule_route(query: str) -> RouteDecision | None:
    normalized = query.lower()
    service_words = ("取消", "退款", "退订", "改期", "投诉", "售后", "订单", "改签")
    booking_words = ("预订", "预定", "下单", "订房", "订酒店", "房型", "报价")
    discovery_words = ("附近", "搜索", "查询", "推荐", "找酒店", "酒店位置", "高德")
    has_service = any(word in normalized for word in service_words)
    has_booking = any(word in normalized for word in booking_words)
    has_discovery = any(word in normalized for word in discovery_words) or (
        "找" in normalized and "酒店" in normalized
    )
    if has_service:
        intent = Intent.COMPLAINT if "投诉" in normalized else Intent.MANAGE_BOOKING
        return RouteDecision(
            intent=intent,
            target_agent=AgentRole.CUSTOMER_SERVICE,
            confidence=0.97,
            reason="检测到订单售后或投诉意图。",
        )
    if has_booking and has_discovery:
        return RouteDecision(
            intent=Intent.DISCOVER_HOTEL,
            target_agent=AgentRole.DISCOVERY,
            confidence=0.94,
            reason="复合请求需要先检索候选酒店，再结构化交接给预订。",
        )
    if has_booking:
        return RouteDecision(
            intent=Intent.BOOK_HOTEL,
            target_agent=AgentRole.BOOKING,
            confidence=0.94,
            reason="检测到报价或预订意图。",
        )
    if has_discovery:
        return RouteDecision(
            intent=Intent.DISCOVER_HOTEL,
            target_agent=AgentRole.DISCOVERY,
            confidence=0.94,
            reason="检测到酒店搜索或推荐意图。",
        )
    if normalized.strip() in {"你好", "您好", "hi", "hello", "帮助", "你能做什么"}:
        return RouteDecision(
            intent=Intent.GENERAL,
            target_agent=AgentRole.DIRECT,
            confidence=0.99,
            reason="普通问候或能力说明无需进入 Specialist。",
        )
    return None


def _tool_result_message(name: str, output: str) -> str:
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        payload = None
    if name == "confirm_hotel_booking" and isinstance(payload, dict):
        order = payload.get("order") or {}
        return (
            f"沙箱预订已处理：**{order.get('hotel_name', '所选酒店')}**，"
            f"订单号 `{order.get('order_id', '-')}`，状态 `{order.get('status', '-')}`，"
            f"金额 ¥{order.get('total_amount', 0)}。\n\n"
            "这是模拟交易，没有产生真实酒店订单或支付。"
        )
    if name == "cancel_booking" and isinstance(payload, dict):
        return (
            f"沙箱订单已取消，退款金额 ¥{payload.get('refund_amount', 0)}，"
            f"退款编号 `{payload.get('refund_id') or '无需退款'}`。"
        )
    if name == "change_booking_dates":
        return "沙箱订单日期已按确认后的参数修改。"
    if name == "request_refund" and isinstance(payload, dict):
        return f"沙箱退款已完成，退款编号 `{payload.get('refund_id', '-')}`。"
    if name == "submit_complaint" and isinstance(payload, dict):
        return f"投诉工单已创建，编号 `{payload.get('complaint_id', '-')}`。"
    return output


def build_agent_graph(
    *,
    models: dict[AgentRole, Any],
    specialist_tools: dict[AgentRole, list[BaseTool]],
    tool_policies: dict[str, ToolPolicy],
    checkpointer: Any,
    store: Any,
    repository: TravelRepository,
    settings: Settings,
    user_id: str,
    session_id: str,
) -> Any:
    """Compile a role-isolated Multi-Agent workflow for one user/session context."""
    specialist_tools = {role: list(items) for role, items in specialist_tools.items()}
    specialist_tools.setdefault(AgentRole.DISCOVERY, []).append(artifact_reader_tool)
    tool_policies[ARTIFACT_READER_NAME] = ToolPolicy(
        AgentRole.DISCOVERY, RiskLevel.READ_ONLY, "读取当前会话工具产物的后续分页"
    )
    tools_by_role = {
        role: {item.name: item for item in items} for role, items in specialist_tools.items()
    }
    bound_models = {
        role: models[role].bind_tools(items) for role, items in specialist_tools.items()
    }

    async def context_node(state: AgentState) -> dict[str, Any]:
        all_tools = [item for items in specialist_tools.values() for item in items]
        return await compress_context(
            state,
            model=models[AgentRole.SUPERVISOR],
            tools=all_tools,
            context_window_tokens=settings.model_context_window_tokens,
            trigger_ratio=settings.context_compression_ratio,
            recent_turns=settings.context_recent_turns,
            summary_max_tokens=settings.context_summary_max_tokens,
        )

    async def supervisor_node(state: AgentState) -> dict[str, Any]:
        query = _latest_human_text(state)
        clock = runtime_clock(settings.app_timezone)
        started = time.perf_counter()
        asks_for_more = query.strip() in {"查看更多", "下一页", "更多", "继续显示"}
        if asks_for_more and state.get("tool_artifact_refs"):
            decision = RouteDecision(
                intent=Intent.DISCOVER_HOTEL,
                target_agent=AgentRole.DISCOVERY,
                confidence=0.99,
                reason="检测到当前会话工具产物的分页请求。",
            )
        else:
            decision = _rule_route(query)
        if decision is None:
            prompt = [
                SystemMessage(
                    content=(
                        "你是酒店平台 Supervisor。只进行路由，不调用工具。"
                        "目标角色只能是 discovery、booking、customer_service 或 direct。"
                        f"\n\n{clock.as_system_prompt()}"
                    )
                ),
                HumanMessage(
                    content=json.dumps(
                        {
                            "current_input": query,
                            "conversation_summary": state.get("conversation_summary") or "",
                            "travel_workflow": state.get("travel_workflow") or {},
                            "recent_handoffs": list(state.get("handoff_history") or [])[-3:],
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            ]
            try:
                router = models[AgentRole.SUPERVISOR].with_structured_output(RouteDecision)
                decision = await router.ainvoke(prompt)
            except Exception:
                logger.exception("Structured supervisor routing failed; asking for clarification")
                decision = RouteDecision(
                    intent=Intent.CLARIFY,
                    target_agent=AgentRole.DIRECT,
                    confidence=0,
                    reason="无法可靠识别当前请求。",
                )
        if decision.confidence < settings.supervisor_confidence_threshold:
            decision = RouteDecision(
                intent=Intent.CLARIFY,
                target_agent=AgentRole.DIRECT,
                confidence=decision.confidence,
                reason=f"路由置信度不足：{decision.reason}",
            )
        sequence = await record_trace(
            repository,
            state,
            event_type="route.selected",
            actor=decision.target_agent.value,
            duration_ms=(time.perf_counter() - started) * 1000,
            input_summary=query,
            output_summary=decision.reason,
            metadata={"intent": decision.intent.value, "confidence": decision.confidence},
        )
        workflow = dict(state.get("travel_workflow") or {})
        workflow.update(
            {
                "objective": workflow.get("objective") or query,
                "intent": decision.intent.value,
                "status": "routed",
                "requested_booking": decision.intent in {Intent.BOOK_HOTEL, Intent.DISCOVER_HOTEL}
                and any(word in query for word in ("预订", "预定", "下单", "订房")),
            }
        )
        normalized_dates = resolve_date_range(query, today=clock.today)
        if normalized_dates is not None:
            workflow.update(
                {
                    "check_in": normalized_dates.check_in.isoformat(),
                    "check_out": normalized_dates.check_out.isoformat(),
                    "date_source": "server_normalized",
                    "date_year_inferred": normalized_dates.inferred_year,
                    "timezone": clock.timezone,
                }
            )
        updates: dict[str, Any] = {
            "route_decision": decision.model_dump(mode="json"),
            "active_agent": decision.target_agent.value,
            "travel_workflow": workflow,
            "trace_sequence": sequence,
        }
        if decision.target_agent == AgentRole.DIRECT:
            content = (
                "你好，我可以协同完成酒店搜索、个性化推荐、沙箱报价与预订，"
                "也能处理沙箱订单的改期、取消、退款和投诉。"
                if decision.intent == Intent.GENERAL
                else "我还不能可靠判断你的目标。请说明是要搜索酒店、预订，还是处理已有订单。"
            )
            updates.update({"messages": [AIMessage(content=content)], "final_response": content})
        return updates

    def route_supervisor(state: AgentState) -> Literal["specialist", "finalize"]:
        return "finalize" if state.get("active_agent") == AgentRole.DIRECT else "specialist"

    async def specialist_node(state: AgentState) -> dict[str, Any]:
        role = AgentRole(str(state.get("active_agent")))
        clock = runtime_clock(settings.app_timezone)
        workflow = dict(state.get("travel_workflow") or {})
        budget_exhausted = workflow.get("tool_budget_exhausted_agent") == role.value
        model_input = [
            SystemMessage(content=ROLE_PROMPTS[role]),
            SystemMessage(content=clock.as_system_prompt()),
            *build_model_input(state, role=role.value),
        ]
        specialist_model = bound_models[role]
        if budget_exhausted:
            model_input.insert(
                2,
                SystemMessage(
                    content=(
                        "本轮只读工具预算已经用完。禁止继续搜索或读取产物；"
                        "请使用已有证据完成回答。若用户要求报价且候选酒店已经明确，"
                        "只能调用结构化 handoff 工具交接给 Booking Agent。"
                    )
                ),
            )
            terminal_tools = [
                item for item in specialist_tools[role] if item.name in HANDOFF_TOOL_TARGETS
            ]
            specialist_model = (
                models[role].bind_tools(terminal_tools) if terminal_tools else models[role]
            )
        estimated = estimate_context_tokens(model_input, specialist_tools[role])
        if estimated > settings.model_max_input_tokens:
            raise ContextWindowExceeded(
                "当前请求与不可裁剪的酒店工作流超过模型输入上限，请缩短本次输入后重试。"
            )
        started = time.perf_counter()
        sequence = await record_trace(
            repository,
            state,
            event_type="agent.entered",
            actor=role.value,
            input_summary=_latest_human_text(state),
        )
        try:
            response = await specialist_model.ainvoke(model_input)
        except Exception as exc:
            await record_trace(
                repository,
                dict(state, trace_sequence=sequence),
                event_type="agent.exited",
                actor=role.value,
                status="error",
                duration_ms=(time.perf_counter() - started) * 1000,
                error_code=type(exc).__name__,
            )
            raise
        usage = getattr(response, "usage_metadata", None) or {}
        token_usage = usage.get("total_tokens") or usage.get("input_tokens")
        sequence = await record_trace(
            repository,
            dict(state, trace_sequence=sequence),
            event_type="agent.exited",
            actor=role.value,
            duration_ms=(time.perf_counter() - started) * 1000,
            token_usage=token_usage,
            output_summary=str(response.content or "tool_calls")[:500],
        )
        updates: dict[str, Any] = {"messages": [response], "trace_sequence": sequence}
        if not response.tool_calls:
            updates["final_response"] = str(response.content)
            updates["travel_workflow"] = {
                **(state.get("travel_workflow") or {}),
                "status": "completed",
            }
        return updates

    def route_specialist(state: AgentState) -> Literal["prepare_tool", "finalize"]:
        message = _latest_ai(state)
        return "prepare_tool" if message and message.tool_calls else "finalize"

    async def prepare_tool_node(state: AgentState) -> dict[str, Any]:
        queue = list(state.get("tool_call_queue") or [])
        if not queue:
            message = _latest_ai(state)
            queue = list(message.tool_calls if message else [])
        if not queue:
            return {"pending_approval": None, "tool_call_queue": []}
        current, remaining = queue[0], queue[1:]
        name = str(current.get("name") or "")
        role = AgentRole(str(state.get("active_agent")))
        if name not in tools_by_role[role]:
            raise PermissionError(f"Tool {name!r} is not available to {role.value}")
        arguments = current.get("args") if isinstance(current.get("args"), dict) else {}
        policy = tool_policies[name]
        pending = {
            "tool_call_id": str(current.get("id") or "unknown-tool-call"),
            "tool_name": name,
            "original_args": arguments,
            "effective_args": arguments,
            "agent_role": role.value,
            "risk_level": policy.risk.value,
            "risk_description": policy.description,
            "requires_approval": policy.risk != RiskLevel.READ_ONLY,
        }
        updates = {
            "pending_approval": pending,
            "tool_call_queue": remaining,
            "approval_decision": None,
            "task_plan": update_task_plan(
                state.get("task_plan"),
                operation_status="waiting_approval"
                if pending["requires_approval"]
                else "in_progress",
                operation_label=f"{role.value} · {name}",
                final_status="pending",
                constraints=arguments,
            ),
        }
        if pending["requires_approval"]:
            updates["trace_sequence"] = await record_trace(
                repository,
                state,
                event_type="approval.requested",
                actor=role.value,
                input_summary=name,
                metadata={"risk_level": policy.risk.value, "arguments": arguments},
            )
        return updates

    def route_prepared(state: AgentState) -> Literal["approval", "execute"]:
        pending = state.get("pending_approval") or {}
        return "approval" if pending.get("requires_approval") else "execute"

    def approval_node(state: AgentState) -> dict[str, Any]:
        pending = state.get("pending_approval") or {}
        response = interrupt(
            {
                "interrupt_type": "tool_approval",
                "action_request": {
                    "action": pending.get("tool_name"),
                    "args": pending.get("effective_args") or {},
                },
                "description": pending.get("risk_description"),
                "agent_role": pending.get("agent_role"),
                "risk_level": pending.get("risk_level"),
                "workflow_state": state.get("travel_workflow") or {},
                "task_state": task_state_for_public(state),
                "trace_id": state.get("trace_id"),
            }
        )
        if not isinstance(response, dict):
            response = {"type": InterruptDecision.RESPONSE, "args": {"args": str(response)}}
        return {"approval_decision": response}

    def route_approval(state: AgentState) -> Literal["execute", "reject", "feedback"]:
        decision = str((state.get("approval_decision") or {}).get("type") or "")
        if decision in {InterruptDecision.ACCEPT, InterruptDecision.EDIT}:
            return "execute"
        if decision == InterruptDecision.REJECT:
            return "reject"
        return "feedback"

    async def execute_tool_node(state: AgentState) -> dict[str, Any]:
        pending = dict(state.get("pending_approval") or {})
        decision_data = dict(state.get("approval_decision") or {})
        decision = str(decision_data.get("type") or "internal")
        name = str(pending.get("tool_name") or "")
        role = AgentRole(str(pending.get("agent_role") or state.get("active_agent")))
        call_id = str(pending.get("tool_call_id") or "unknown-tool-call")
        arguments = dict(pending.get("effective_args") or {})
        if decision == InterruptDecision.EDIT:
            nested = decision_data.get("args") or {}
            edited = nested.get("args") if isinstance(nested, dict) else None
            if not isinstance(edited, dict):
                raise ValueError("Edited tool arguments must be a JSON object")
            arguments = edited
        policy = tool_policies[name]
        fingerprint = _tool_fingerprint(role, name, arguments)
        call_history = list(state.get("tool_call_history") or [])
        call_counts = dict(state.get("tool_call_counts") or {})
        role_call_count = int(call_counts.get(role.value) or 0)
        is_bounded_read = policy.risk == RiskLevel.READ_ONLY and name not in HANDOFF_TOOL_TARGETS
        duplicate_call = is_bounded_read and fingerprint in call_history
        budget_exhausted = (
            is_bounded_read
            and role_call_count >= settings.max_read_only_tool_calls_per_agent
        )
        if duplicate_call or budget_exhausted:
            reason = (
                "检测到相同工具与参数已经执行，本次重复调用已停止。"
                if duplicate_call
                else "本轮只读工具调用已达到安全预算，后续检索已停止。"
            )
            sequence = await record_trace(
                repository,
                state,
                event_type="tool.skipped",
                actor=role.value,
                output_summary=reason,
                metadata={
                    "tool": name,
                    "arguments": arguments,
                    "reason": "duplicate" if duplicate_call else "budget_exhausted",
                },
            )
            workflow = {
                **(state.get("travel_workflow") or {}),
                "tool_budget_exhausted_agent": role.value,
                "status": "evidence_ready",
            }
            return {
                "messages": [ToolMessage(content=reason, tool_call_id=call_id)],
                "pending_approval": None,
                "travel_workflow": workflow,
                "trace_sequence": sequence,
                "task_plan": update_task_plan(
                    state.get("task_plan"),
                    operation_status="completed",
                    operation_label=f"{role.value} · 检索预算已收敛",
                    final_status="in_progress",
                    constraints=arguments,
                ),
            }
        if name in HANDOFF_TOOL_TARGETS:
            history = list(state.get("handoff_history") or [])
            target = HANDOFF_TOOL_TARGETS[name]
            if len(history) >= settings.max_handoffs_per_task:
                content = "Agent 交接次数超过安全上限，请拆分请求后重试。"
                return {
                    "messages": [
                        ToolMessage(content=content, tool_call_id=call_id),
                        AIMessage(content=content),
                    ],
                    "pending_approval": None,
                    "tool_call_queue": [],
                    "final_response": content,
                    "travel_workflow": {
                        **(state.get("travel_workflow") or {}),
                        "status": "handoff_limit",
                    },
                }
            handoff_constraints = _coerce_handoff_constraints(arguments.get("constraints"))
            envelope = HandoffEnvelope(
                source=role,
                target=target,
                objective=str(arguments.get("objective") or _latest_human_text(state)),
                constraints=handoff_constraints,
                quote_id=arguments.get("quote_id"),
                order_id=arguments.get("order_id"),
                reason=f"{role.value} 请求交接给 {target.value}",
            )
            history.append(envelope.model_dump(mode="json"))
            workflow_arguments = {
                key: value
                for key, value in arguments.items()
                if key != "constraints" and value is not None
            }
            workflow = {
                **(state.get("travel_workflow") or {}),
                **workflow_arguments,
                "constraints": handoff_constraints,
                "status": "handed_off",
            }
            sequence = await record_trace(
                repository,
                state,
                event_type="agent.handoff",
                actor=target.value,
                input_summary=role.value,
                output_summary=envelope.reason,
                metadata=envelope.model_dump(mode="json"),
            )
            return {
                "messages": [ToolMessage(content=envelope.model_dump_json(), tool_call_id=call_id)],
                "active_agent": target.value,
                "handoff_history": history,
                "travel_workflow": workflow,
                "pending_approval": None,
                "tool_call_queue": [],
                "trace_sequence": sequence,
            }
        trace_state: AgentState | dict[str, Any] = state
        if decision in {InterruptDecision.ACCEPT, InterruptDecision.EDIT}:
            approval_sequence = await record_trace(
                repository,
                state,
                event_type=(
                    "approval.edited" if decision == InterruptDecision.EDIT else "approval.accepted"
                ),
                actor=role.value,
                input_summary=name,
                metadata={"arguments": arguments},
            )
            trace_state = dict(state, trace_sequence=approval_sequence)
        started = time.perf_counter()
        sequence = await record_trace(
            repository,
            trace_state,
            event_type="tool.started",
            actor=role.value,
            input_summary=name,
            metadata={"tool": name, "arguments": arguments},
        )
        try:
            if name == ARTIFACT_READER_NAME:
                output = await read_artifact_page(
                    store,
                    user_id=user_id,
                    session_id=session_id,
                    artifact_id=str(arguments.get("artifact_id") or ""),
                    page=int(arguments.get("page") or 1),
                    page_size=settings.tool_artifact_page_size,
                    poi_id=str(arguments.get("poi_id") or "") or None,
                    name=str(arguments.get("name") or "") or None,
                )
                reference = None
            else:
                raw_output = await tools_by_role[role][name].ainvoke(arguments)
                output, reference = await persist_tool_result(
                    store,
                    user_id=user_id,
                    session_id=session_id,
                    tool_name=name,
                    tool_call_id=call_id,
                    arguments=arguments,
                    result=raw_output,
                    inline_max_bytes=settings.tool_artifact_inline_max_bytes,
                    page_size=settings.tool_artifact_page_size,
                )
            status = "success"
            error_code = None
        except Exception as exc:
            logger.exception("Specialist tool failed role=%s tool=%s", role.value, name)
            output = f"工具 `{name}` 执行失败：{str(exc)}"
            reference = None
            status = "error"
            error_code = type(exc).__name__
        sequence = await record_trace(
            repository,
            dict(state, trace_sequence=sequence),
            event_type="tool.completed",
            actor=role.value,
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
            output_summary=output,
            error_code=error_code,
            metadata={"tool": name},
        )
        messages: list[Any] = [ToolMessage(content=output, tool_call_id=call_id)]
        references = list(state.get("tool_artifact_refs") or [])
        if reference:
            references.append(reference)
        workflow = dict(state.get("travel_workflow") or {})
        parsed = None
        with suppress(TypeError, json.JSONDecodeError):
            parsed = json.loads(output)
        if name == "prepare_hotel_quote" and isinstance(parsed, dict):
            workflow.update({"quote_id": parsed.get("quote_id"), "status": "quoted"})
        elif name == "confirm_hotel_booking" and isinstance(parsed, dict):
            workflow.update(
                {
                    "order_id": (parsed.get("order") or {}).get("order_id"),
                    "status": (parsed.get("order") or {}).get("status", status),
                }
            )
        elif name == ARTIFACT_READER_NAME and isinstance(parsed, dict):
            references = [
                (
                    {**item, "last_page": parsed.get("page")}
                    if item.get("artifact_id") == parsed.get("artifact_id")
                    else item
                )
                for item in references
            ]
            workflow.update(
                {
                    "artifact_id": parsed.get("artifact_id"),
                    "artifact_page": parsed.get("page"),
                    "status": "artifact_page_read",
                }
            )
        updates: dict[str, Any] = {
            "messages": messages,
            "pending_approval": None,
            "tool_artifact_refs": references,
            "travel_workflow": workflow,
            "trace_sequence": sequence,
            "task_plan": update_task_plan(
                state.get("task_plan"),
                operation_status="failed" if status == "error" else "completed",
                final_status="pending",
                constraints=arguments,
            ),
        }
        if policy.risk != RiskLevel.READ_ONLY and not state.get("tool_call_queue"):
            final_content = _tool_result_message(name, output)
            messages.append(AIMessage(content=final_content))
            updates.update(
                {
                    "messages": messages,
                    "tool_call_queue": [],
                    "final_response": final_content,
                    "execution_error": error_code if status == "error" else None,
                    "task_plan": update_task_plan(
                        updates["task_plan"],
                        operation_status="failed" if status == "error" else "completed",
                        final_status="failed" if status == "error" else "completed",
                    ),
                }
            )
        if is_bounded_read:
            call_history.append(fingerprint)
            call_counts[role.value] = role_call_count + 1
            updates.update(
                {
                    "tool_call_history": call_history,
                    "tool_call_counts": call_counts,
                }
            )
        return updates

    def route_after_execute(state: AgentState) -> Literal["prepare_tool", "specialist", "finalize"]:
        if state.get("final_response"):
            return "finalize"
        if state.get("tool_call_queue"):
            return "prepare_tool"
        return "specialist"

    async def reject_node(state: AgentState) -> dict[str, Any]:
        pending = state.get("pending_approval") or {}
        name = str(pending.get("tool_name") or "")
        content = "已拒绝本次沙箱交易操作，未创建或修改任何订单。"
        sequence = await record_trace(
            repository,
            state,
            event_type="approval.rejected",
            actor=str(pending.get("agent_role") or "unknown"),
            output_summary=name,
        )
        return {
            "messages": [
                ToolMessage(content=content, tool_call_id=str(pending.get("tool_call_id"))),
                AIMessage(content=content),
            ],
            "pending_approval": None,
            "tool_call_queue": [],
            "final_response": content,
            "trace_sequence": sequence,
            "travel_workflow": {
                **(state.get("travel_workflow") or {}),
                "status": "rejected",
            },
        }

    async def feedback_node(state: AgentState) -> dict[str, Any]:
        pending = state.get("pending_approval") or {}
        raw = (state.get("approval_decision") or {}).get("args") or {}
        feedback = raw.get("args") if isinstance(raw, dict) else raw
        sequence = await record_trace(
            repository,
            state,
            event_type="approval.feedback",
            actor=str(pending.get("agent_role") or "unknown"),
            input_summary=str(feedback or ""),
        )
        return {
            "messages": [
                ToolMessage(
                    content=f"用户反馈：{feedback or '未提供补充信息。'}",
                    tool_call_id=str(pending.get("tool_call_id")),
                )
            ],
            "pending_approval": None,
            "tool_call_queue": [],
            "trace_sequence": sequence,
        }

    async def finalize_node(state: AgentState) -> dict[str, Any]:
        execution_error = state.get("execution_error")
        sequence = await record_trace(
            repository,
            state,
            event_type="task.completed",
            actor=str(state.get("active_agent") or AgentRole.SUPERVISOR.value),
            status="error" if execution_error else "success",
            output_summary=state.get("final_response"),
            error_code=str(execution_error) if execution_error else None,
            metadata={"workflow": state.get("travel_workflow") or {}},
        )
        return {"trace_sequence": sequence}

    builder = StateGraph(AgentState)
    for name, node in (
        ("context", context_node),
        ("supervisor", supervisor_node),
        ("specialist", specialist_node),
        ("prepare_tool", prepare_tool_node),
        ("approval", approval_node),
        ("execute", execute_tool_node),
        ("reject", reject_node),
        ("feedback", feedback_node),
        ("finalize", finalize_node),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "context")
    builder.add_edge("context", "supervisor")
    builder.add_conditional_edges(
        "supervisor", route_supervisor, {"specialist": "specialist", "finalize": "finalize"}
    )
    builder.add_conditional_edges(
        "specialist",
        route_specialist,
        {"prepare_tool": "prepare_tool", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "prepare_tool", route_prepared, {"approval": "approval", "execute": "execute"}
    )
    builder.add_conditional_edges(
        "approval",
        route_approval,
        {"execute": "execute", "reject": "reject", "feedback": "feedback"},
    )
    builder.add_conditional_edges(
        "execute",
        route_after_execute,
        {
            "prepare_tool": "prepare_tool",
            "specialist": "specialist",
            "finalize": "finalize",
        },
    )
    builder.add_edge("reject", "finalize")
    builder.add_edge("feedback", "specialist")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer, store=store)
