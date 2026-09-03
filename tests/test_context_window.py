from __future__ import annotations

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from travel_agent_orchestrator.agent.context import build_model_input, compress_context
from travel_agent_orchestrator.agent.runtime import conversation_thread_id, graph_config
from travel_agent_orchestrator.agent.state import new_task_plan


class FakeSummaryModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list] = []

    def bind(self, **_):
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self.response)


def test_graph_checkpoint_is_versioned_and_user_scoped() -> None:
    assert graph_config("user-7", "session-42") == {
        "configurable": {"thread_id": "travel-agent-orchestrator:v1:user-7:session-42"}
    }
    assert conversation_thread_id("other-user", "session-42") != conversation_thread_id(
        "user-7", "session-42"
    )


def test_model_input_orders_durable_state_before_unsummarized_messages() -> None:
    old = HumanMessage(content="已摘要问题", id="old")
    current = HumanMessage(content="当前问题", id="current")
    prepared = build_model_input(
        {
            "messages": [old, AIMessage(content="旧回答", id="old-answer"), current],
            "system_prompt": "系统提示",
            "long_term_memory": "偏好安静",
            "conversation_summary": "用户此前讨论酒店",
            "summary_cursor": "old-answer",
            "key_facts": ["预算 500 元"],
            "task_plan": new_task_plan("继续查询"),
        }
    )

    assert isinstance(prepared[0], SystemMessage)
    assert prepared[0].content == "系统提示"
    assert "用户长期记忆" in prepared[1].content
    assert "此前对话摘要" in prepared[2].content
    assert "预算 500 元" in prepared[3].content
    assert prepared[-1].content == "当前问题"
    assert all(message.content != "已摘要问题" for message in prepared)


def test_role_context_projection_hides_unrelated_workflow_and_artifacts() -> None:
    state = {
        "messages": [HumanMessage(content="处理当前任务")],
        "long_term_memory": "偏好安静",
        "task_plan": new_task_plan("处理订单"),
        "travel_workflow": {
            "objective": "处理订单",
            "city": "杭州",
            "keywords": "亲子酒店",
            "order_id": "ord-secret",
            "refund_id": "ref-secret",
            "quote_id": "quo-booking",
        },
        "tool_artifact_refs": [{"artifact_id": "amap-result"}],
    }

    discovery = "\n".join(str(item.content) for item in build_model_input(state, role="discovery"))
    booking = "\n".join(str(item.content) for item in build_model_input(state, role="booking"))
    customer_service = "\n".join(
        str(item.content) for item in build_model_input(state, role="customer_service")
    )

    assert "亲子酒店" in discovery and "amap-result" in discovery
    assert "ord-secret" not in discovery and "ref-secret" not in discovery
    assert "quo-booking" in booking and "amap-result" not in booking
    assert "ord-secret" in customer_service and "ref-secret" in customer_service
    assert "亲子酒店" not in customer_service and "偏好安静" not in customer_service


def test_compression_keeps_raw_messages_and_advances_cursor() -> None:
    messages = [
        HumanMessage(content="第一轮问题" + "甲" * 120, id="h1"),
        AIMessage(content="第一轮回答" + "乙" * 120, id="a1"),
        HumanMessage(content="第二轮问题", id="h2"),
        AIMessage(content="第二轮回答", id="a2"),
    ]
    model = FakeSummaryModel(
        json.dumps(
            {"summary": "第一轮讨论已完成。", "key_facts": ["用户确认第一轮结论"]},
            ensure_ascii=False,
        )
    )
    state = {
        "messages": messages,
        "system_prompt": "系统提示",
        "task_plan": new_task_plan("第二轮问题"),
    }

    updates = asyncio.run(
        compress_context(
            state,
            model=model,
            tools=[],
            context_window_tokens=100,
            trigger_ratio=0.5,
            recent_turns=1,
            summary_max_tokens=200,
        )
    )

    assert state["messages"] == messages
    assert updates["conversation_summary"] == "第一轮讨论已完成。"
    assert updates["summary_cursor"] == "a1"
    assert updates["recent_message_ids"] == ["h2", "a2"]
    assert "用户确认第一轮结论" in updates["key_facts"]


def test_context_below_trigger_does_not_call_summary_model() -> None:
    model = FakeSummaryModel('{"summary":"不应调用","key_facts":[]}')
    updates = asyncio.run(
        compress_context(
            {"messages": [HumanMessage(content="短问题", id="h1")]},
            model=model,
            tools=[],
            context_window_tokens=10_000,
            trigger_ratio=0.7,
            recent_turns=5,
            summary_max_tokens=200,
        )
    )

    assert model.calls == []
    assert "conversation_summary" not in updates


def test_invalid_summary_keeps_previous_summary_and_cursor() -> None:
    model = FakeSummaryModel("not-json")
    state = {
        "messages": [
            HumanMessage(content="旧问题" + "甲" * 120, id="h1"),
            AIMessage(content="旧回答" + "乙" * 120, id="a1"),
            HumanMessage(content="当前问题", id="h2"),
        ],
        "conversation_summary": "原摘要",
        "summary_cursor": None,
    }
    updates = asyncio.run(
        compress_context(
            state,
            model=model,
            tools=[],
            context_window_tokens=100,
            trigger_ratio=0.5,
            recent_turns=1,
            summary_max_tokens=200,
        )
    )

    assert "conversation_summary" not in updates
    assert "summary_cursor" not in updates
    assert state["conversation_summary"] == "原摘要"


def test_compression_never_splits_tool_call_and_tool_message() -> None:
    tool_call = {
        "name": "maps_text_search",
        "args": {"keywords": "酒店"},
        "id": "call-1",
        "type": "tool_call",
    }
    messages = [
        HumanMessage(content="查酒店" + "甲" * 120, id="h1"),
        AIMessage(content="", tool_calls=[tool_call], id="a1"),
        ToolMessage(content="查询结果", tool_call_id="call-1", id="t1"),
        AIMessage(content="已找到酒店", id="a2"),
        HumanMessage(content="继续", id="h2"),
    ]
    model = FakeSummaryModel('{"summary":"已查询酒店。","key_facts":[]}')

    asyncio.run(
        compress_context(
            {"messages": messages, "task_plan": new_task_plan("继续")},
            model=model,
            tools=[],
            context_window_tokens=100,
            trigger_ratio=0.5,
            recent_turns=1,
            summary_max_tokens=200,
        )
    )

    summary_request = json.loads(model.calls[0][-1].content)
    merged = summary_request["messages_to_merge"]
    assert any(item["type"] == "ai" and item["data"]["tool_calls"] for item in merged)
    assert any(item["type"] == "tool" for item in merged)
