from __future__ import annotations

from dataclasses import dataclass

from travel_agent_orchestrator.agent.results import (
    build_agent_response,
    filter_last_human_conversation,
)


@dataclass
class InterruptValue:
    value: dict


def test_completed_result_keeps_only_latest_human_turn_for_task_summary() -> None:
    response = build_agent_response(
        "session",
        "task",
        {
            "messages": [
                {"type": "human", "content": "first"},
                {"type": "ai", "content": "one"},
                {"type": "human", "content": "second"},
                {"type": "ai", "content": "two"},
            ]
        },
    )

    filtered = filter_last_human_conversation(response)

    assert filtered["task_id"] == "task"
    assert [message["content"] for message in filtered["result"]["messages"]] == [
        "second",
        "two",
    ]


def test_interrupt_result_is_normalized() -> None:
    response = build_agent_response(
        "session",
        "task",
        {"__interrupt__": [InterruptValue({"description": "review"})]},
    )

    assert response.status == "interrupted"
    assert response.interrupt_data == {
        "description": "review",
        "interrupt_type": "tool_approval",
    }


def test_terminal_tool_failure_is_not_reported_as_completed() -> None:
    response = build_agent_response(
        "session",
        "task",
        {
            "messages": [{"type": "ai", "content": "报价不存在。"}],
            "final_response": "报价不存在。",
            "execution_error": "DomainRuleError",
        },
    )

    assert response.status == "error"
    assert response.message == "报价不存在。"
    assert response.result["execution_error"] == "DomainRuleError"
