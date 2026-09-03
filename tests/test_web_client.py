from __future__ import annotations

import json

import httpx
import pytest

from travel_agent_orchestrator.web.client import AgentApiClient, ApiClientError


def test_unavailable_service_has_actionable_message() -> None:
    def unavailable(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    http_client = httpx.Client(transport=httpx.MockTransport(unavailable))
    client = AgentApiClient("http://localhost:8002", 1, client=http_client)

    with pytest.raises(ApiClientError, match="确认 API 已启动"):
        client.get_system_info()


def test_successful_http_response_must_contain_json_object() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, text="not-json", headers={"content-type": "text/plain"})
    )
    client = AgentApiClient("http://localhost:8002", 1, client=httpx.Client(transport=transport))

    with pytest.raises(ApiClientError, match="无法解析"):
        client.get_system_info()


@pytest.mark.parametrize(
    ("decision", "args"),
    [
        ("accept", None),
        ("reject", None),
        ("edit", {"args": {"level": 4}}),
        ("response", {"args": "请推荐其他方案"}),
    ],
)
def test_resume_preserves_public_hitl_payload(
    decision: str, args: dict[str, object] | None
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"task_id": "task"})

    client = AgentApiClient(
        "http://localhost:8002",
        1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.resume("user", "session", "task", decision, args)

    assert captured["response_type"] == decision
    assert captured["args"] == args


def test_inspector_requests_use_read_only_encoded_paths() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200, json={})

    client = AgentApiClient(
        "http://localhost:8002",
        1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.get_trace("user/name", "session one", "task?1")
    client.get_orders("user/name")
    client.get_metrics()
    client.get_latest_evaluation()

    assert paths == [
        "/agent/trace/user%2Fname/session%20one/task%3F1",
        "/travel/orders/user%2Fname",
        "/system/metrics",
        "/evaluation/latest",
    ]
