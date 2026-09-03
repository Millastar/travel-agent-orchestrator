"""Run free deterministic checks and optional LLM answer-quality judging."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from travel_agent_orchestrator.agent.graph import _rule_route
from travel_agent_orchestrator.evaluation.dataset import SCENARIOS
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.infrastructure.llm import get_chat_model
from travel_agent_orchestrator.travel.models import AgentRole, Hotel, RiskLevel, RoomOffer
from travel_agent_orchestrator.travel.repository import InMemoryTravelRepository
from travel_agent_orchestrator.travel.service import DomainRuleError, HotelSandboxService
from travel_agent_orchestrator.travel.tools import build_specialist_tools


def _evaluation_repository() -> InMemoryTravelRepository:
    hotel = Hotel(
        hotel_id="eval-hotel",
        name="离线评测酒店",
        city="杭州",
        address="测试路 1 号",
        stars=4,
    )
    repository = InMemoryTravelRepository([hotel])
    repository.rooms["eval-room"] = RoomOffer(
        room_id="eval-room",
        hotel_id=hotel.hotel_id,
        room_type="标准大床房",
        nightly_rate=500,
        capacity=2,
        available_rooms=8,
        cancellation_policy="入住前一天可免费取消。",
    )
    return repository


async def _run_invariant_probes(settings: Settings) -> dict[str, Any]:
    """Run free domain and policy probes against in-memory components."""
    repository = _evaluation_repository()
    service = HotelSandboxService(repository)
    offline_settings = settings.model_copy(update={"amap_maps_api_key": None})
    tools, policies = await build_specialist_tools(
        offline_settings,
        service,
        user_id="evaluation-user",
        task_id="evaluation-task",
    )
    tool_names = {role: {item.name for item in items} for role, items in tools.items()}
    selection_checks = (
        "search_sandbox_hotels" in tool_names[AgentRole.DISCOVERY],
        "confirm_hotel_booking" not in tool_names[AgentRole.DISCOVERY],
        "confirm_hotel_booking" in tool_names[AgentRole.BOOKING],
        "cancel_booking" not in tool_names[AgentRole.BOOKING],
        "cancel_booking" in tool_names[AgentRole.CUSTOMER_SERVICE],
        "request_refund" in tool_names[AgentRole.CUSTOMER_SERVICE],
    )
    write_policies = [policy for policy in policies.values() if policy.risk != RiskLevel.READ_ONLY]
    approval_checks = (
        len(write_policies) == 5,
        all(
            policy.risk in {RiskLevel.TRANSACTION, RiskLevel.DESTRUCTIVE}
            for policy in write_policies
        ),
    )

    check_in = date.today() + timedelta(days=7)
    quote = await service.create_quote(
        user_id="evaluation-user",
        hotel="eval-hotel",
        check_in=check_in,
        check_out=check_in + timedelta(days=2),
        guests=2,
    )
    order, _ = await service.confirm_booking(
        user_id="evaluation-user",
        quote_id=quote.quote_id,
        idempotency_key="evaluation-idempotency-key",
    )
    replay, replay_data = await service.confirm_booking(
        user_id="evaluation-user",
        quote_id=quote.quote_id,
        idempotency_key="evaluation-idempotency-key",
    )
    parameter_checks = (
        order.hotel_name == quote.hotel.name,
        order.check_in == quote.check_in,
        order.check_out == quote.check_out,
        order.total_amount == quote.total_amount,
        replay.order_id == order.order_id and replay_data.get("idempotent_replay") is True,
    )

    cross_user_blocked = False
    try:
        await service.get_order("different-user", order.order_id)
    except DomainRuleError:
        cross_user_blocked = True

    cancelled = await service.cancel_booking(
        user_id="evaluation-user",
        order_id=order.order_id,
        reason="offline evaluation",
    )
    illegal_transition_blocked = False
    try:
        await service.change_dates(
            user_id="evaluation-user",
            order_id=order.order_id,
            check_in=check_in + timedelta(days=1),
            check_out=check_in + timedelta(days=3),
        )
    except DomainRuleError:
        illegal_transition_blocked = True

    compensation_repository = _evaluation_repository()
    compensation_service = HotelSandboxService(
        compensation_repository,
        payment_mode="fail_after_capture",
    )
    compensation_quote = await compensation_service.create_quote(
        user_id="evaluation-user",
        hotel="eval-hotel",
        check_in=check_in,
        check_out=check_in + timedelta(days=1),
    )
    compensated_order, saga = await compensation_service.confirm_booking(
        user_id="evaluation-user",
        quote_id=compensation_quote.quote_id,
        idempotency_key="evaluation-compensation",
    )
    compensation_succeeded = (
        saga.get("compensated") is True and compensated_order.status == "refunded"
    )

    checks = [
        *selection_checks,
        *approval_checks,
        *parameter_checks,
        cross_user_blocked,
        illegal_transition_blocked,
        compensation_succeeded,
        bool(cancelled.get("sandbox")),
    ]
    return {
        "tool_selection_accuracy": sum(selection_checks) / len(selection_checks),
        "parameter_consistency": sum(parameter_checks) / len(parameter_checks),
        "write_approval_coverage": sum(approval_checks) / len(approval_checks),
        "illegal_state_transition_rejection_rate": float(illegal_transition_blocked),
        "cross_user_data_leakage_rate": 0.0 if cross_user_blocked else 1.0,
        "compensation_success_rate": float(compensation_succeeded),
        "task_success_rate": sum(checks) / len(checks),
        "probe_count": len(checks),
    }


async def _judge_examples(settings: Settings) -> dict[str, Any]:
    model = get_chat_model(settings, "supervisor")
    examples = (
        (
            "用户拒绝了沙箱预订",
            "已拒绝本次沙箱交易操作，未创建或修改任何订单。",
        ),
        (
            "支付成功但供应商提交失败",
            "支付已自动退回，订单未确认；这是模拟交易。",
        ),
    )
    scores: list[float] = []
    for scenario, answer in examples:
        response = await model.ainvoke(
            [
                SystemMessage(content="按相关性、事实依据和风险表达给回答打 1-5 分，只输出数字。"),
                HumanMessage(content=f"场景：{scenario}\n回答：{answer}"),
            ]
        )
        try:
            scores.append(float(str(response.content).strip()))
        except ValueError:
            scores.append(0)
    return {"cases": len(scores), "average_score": sum(scores) / len(scores)}


async def run_evaluation(
    *, settings: Settings | None = None, judge: bool = False, output_dir: Path | None = None
) -> dict[str, Any]:
    """Evaluate committed scenarios without network calls unless judge is explicitly enabled."""
    passed = 0
    failures: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        decision = _rule_route(scenario.query)
        actual_agent = decision.target_agent if decision else None
        actual_intent = decision.intent if decision else None
        ok = actual_agent == scenario.expected_agent and actual_intent == scenario.expected_intent
        passed += int(ok)
        if not ok:
            failures.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "expected_agent": scenario.expected_agent,
                    "actual_agent": actual_agent,
                    "expected_intent": scenario.expected_intent,
                    "actual_intent": actual_intent,
                }
            )
    handoff_cases = [item for item in SCENARIOS if item.category == "handoff"]
    handoff_passed = sum(
        _rule_route(item.query) is not None
        and _rule_route(item.query).target_agent == item.expected_agent
        for item in handoff_cases
    )
    resolved_settings = settings or Settings()
    invariant_metrics = await _run_invariant_probes(resolved_settings)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": "committed deterministic fixtures",
        "scenario_count": len(SCENARIOS),
        "route_accuracy": round(passed / len(SCENARIOS), 4),
        "handoff_first_hop_accuracy": round(handoff_passed / len(handoff_cases), 4),
        "deterministic_metrics": invariant_metrics,
        "passed": passed,
        "failures": failures,
        "llm_judge": {"enabled": False, "reason": "Run with --judge to use model quota."},
    }
    if judge:
        report["llm_judge"] = {
            "enabled": True,
            **await _judge_examples(resolved_settings),
        }
    destination = output_dir or Path("var/evaluations")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    markdown = (
        "# Offline Evaluation\n\n"
        f"- Dataset: `{report['dataset']}`\n"
        f"- Scenarios: {report['scenario_count']}\n"
        f"- Deterministic route accuracy: {report['route_accuracy']:.2%}\n"
        f"- Handoff first-hop accuracy: {report['handoff_first_hop_accuracy']:.2%}\n"
        f"- Tool selection accuracy: {invariant_metrics['tool_selection_accuracy']:.2%}\n"
        f"- Parameter consistency: {invariant_metrics['parameter_consistency']:.2%}\n"
        f"- Write approval coverage: {invariant_metrics['write_approval_coverage']:.2%}\n"
        "- Illegal transition rejection: "
        f"{invariant_metrics['illegal_state_transition_rejection_rate']:.2%}\n"
        f"- Cross-user leakage rate: {invariant_metrics['cross_user_data_leakage_rate']:.2%}\n"
        f"- Compensation success: {invariant_metrics['compensation_success_rate']:.2%}\n"
        f"- Failures: {len(failures)}\n"
        "\n> This report uses committed fixtures and does not represent production traffic.\n"
    )
    (destination / "latest.md").write_text(markdown, encoding="utf-8")
    return report
