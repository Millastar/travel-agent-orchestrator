from __future__ import annotations

import pytest

from travel_agent_orchestrator.web.presentation import (
    arguments_to_rows,
    build_chat_messages,
    extract_answer,
    extract_interrupt,
    format_interrupt_task_state,
    parse_argument_rows,
    parse_json_arguments,
    polling_timed_out,
    task_from_payload,
)


def test_answer_and_interrupt_payloads_are_normalized() -> None:
    completed = {"last_response": {"result": {"messages": [{"content": "预订已完成。"}]}}}
    interrupted = {
        "last_response": {
            "interrupt_data": {
                "description": "请确认酒店级别",
                "action_request": {
                    "action": "confirm_hotel_booking",
                    "args": {"hotel_name": "五星级酒店"},
                },
            }
        }
    }

    assert extract_answer(completed) == "预订已完成。"
    assert extract_interrupt(interrupted) == (
        "confirm_hotel_booking",
        "请确认酒店级别",
        {"hotel_name": "五星级酒店"},
    )


def test_field_and_json_editors_preserve_value_types() -> None:
    source = {"keyword": "西湖", "radius": 3000, "open": True, "tags": ["酒店"]}
    rows = arguments_to_rows(source)

    assert parse_argument_rows(rows) == source
    assert parse_json_arguments('{"rating": 4, "options": {"parking": true}}') == {
        "rating": 4,
        "options": {"parking": True},
    }


def test_approval_card_formats_persisted_task_state() -> None:
    payload = {
        "last_response": {
            "interrupt_data": {
                "task_state": {
                    "objective": "查询西湖附近酒店",
                    "plan": [
                        {"step": "理解请求", "status": "completed"},
                        {"step": "调用地图", "status": "waiting_approval"},
                    ],
                    "constraints": {"radius": 1000, "keywords": "酒店"},
                }
            }
        }
    }

    rendered = format_interrupt_task_state(payload)

    assert "查询西湖附近酒店" in rendered
    assert "✓ 理解请求" in rendered
    assert "● 调用地图" in rendered
    assert '"radius": 1000' in rendered


@pytest.mark.parametrize("value", ["[]", '"text"', "invalid"])
def test_json_editor_rejects_non_object_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_json_arguments(value)


def test_history_contains_only_user_query_and_presentable_result() -> None:
    task = task_from_payload(
        "task",
        "completed",
        {
            "status": "completed",
            "last_query": "查询西湖附近酒店",
            "last_updated": 100,
            "last_response": {
                "result": {
                    "messages": [
                        {"type": "tool", "content": "internal data"},
                        {"type": "ai", "content": "已找到三家附近酒店。"},
                    ]
                }
            },
        },
    )

    assert build_chat_messages([task]) == [
        {"role": "user", "content": "查询西湖附近酒店"},
        {
            "role": "assistant",
            "content": "已找到三家附近酒店。",
            "metadata": {"title": "TRAVEL AGENT"},
        },
    ]


def test_polling_timeout_uses_explicit_deadline() -> None:
    assert polling_timed_out(10, 120, now=129.9) is False
    assert polling_timed_out(10, 120, now=130) is True
    assert polling_timed_out(0, 120, now=1000) is False
