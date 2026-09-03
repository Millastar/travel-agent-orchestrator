from __future__ import annotations

import asyncio
import json

from tests.fakes import FakeMemoryStore

from travel_agent_orchestrator.agent.artifacts import persist_tool_result, read_artifact_page


def _pois(count: int) -> dict:
    return {
        "pois": [
            {
                "id": f"poi-{index}",
                "name": f"酒店 {index}",
                "address": f"测试路 {index} 号",
                "distance": index * 100,
                "location": f"120.{index},30.{index}",
                "type": "住宿服务;宾馆酒店",
                "tel": "0571-12345678",
                "business": {"rating": "4.8", "cost": "520"},
                "typecode": "100100",
                "photo": f"https://example.test/{index}.jpg",
            }
            for index in range(count)
        ]
    }


def test_amap_result_is_persisted_and_projected_to_safe_ranking_fields() -> None:
    store = FakeMemoryStore()
    output, reference = asyncio.run(
        persist_tool_result(
            store,
            user_id="user-1",
            session_id="session-1",
            tool_name="maps_around_search",
            tool_call_id="call-1",
            arguments={"keywords": "酒店", "radius": 1000},
            result=_pois(8),
            inline_max_bytes=8192,
            page_size=5,
        )
    )

    payload = json.loads(output)
    assert payload["shown"] == 5
    assert payload["total"] == 8
    assert payload["has_more"] is True
    assert reference == {
        "artifact_id": payload["artifact_id"],
        "tool_name": "maps_around_search",
        "total": 8,
        "shown": 5,
        "last_page": 1,
    }
    assert set(payload["summary"][0]) == {
        "poi_id",
        "name",
        "address",
        "distance",
        "location",
        "rating",
        "category",
    }
    assert "typecode" not in payload["summary"][0]
    assert "photo" not in payload["summary"][0]

    page_two = json.loads(
        asyncio.run(
            read_artifact_page(
                store,
                user_id="user-1",
                session_id="session-1",
                artifact_id=payload["artifact_id"],
                page=2,
                page_size=5,
            )
        )
    )
    assert page_two["shown"] == 3
    assert page_two["summary"][0]["name"] == "酒店 5"

    detail = json.loads(
        asyncio.run(
            read_artifact_page(
                store,
                user_id="user-1",
                session_id="session-1",
                artifact_id=payload["artifact_id"],
                page=1,
                page_size=5,
                poi_id="poi-0",
            )
        )
    )
    assert detail["detail"] == {
        "poi_id": "poi-0",
        "name": "酒店 0",
        "address": "测试路 0 号",
        "distance": 0,
        "location": "120.0,30.0",
        "rating": "4.8",
        "category": "住宿服务;宾馆酒店",
        "telephone": "0571-12345678",
        "average_cost": "520",
    }
    assert "photo" not in detail["detail"]
    assert "typecode" not in detail["detail"]


def test_artifact_access_is_scoped_to_exact_user_and_session() -> None:
    store = FakeMemoryStore()
    output, _ = asyncio.run(
        persist_tool_result(
            store,
            user_id="user-1",
            session_id="session-1",
            tool_name="maps_text_search",
            tool_call_id="call-1",
            arguments={},
            result=_pois(1),
            inline_max_bytes=8192,
            page_size=5,
        )
    )
    artifact_id = json.loads(output)["artifact_id"]

    foreign = json.loads(
        asyncio.run(
            read_artifact_page(
                store,
                user_id="user-10",
                session_id="session-1",
                artifact_id=artifact_id,
                page=1,
                page_size=5,
            )
        )
    )
    assert "不属于当前会话" in foreign["error"]


def test_large_generic_results_use_artifacts_but_small_results_stay_inline() -> None:
    store = FakeMemoryStore()
    small, small_reference = asyncio.run(
        persist_tool_result(
            store,
            user_id="user",
            session_id="session",
            tool_name="search_sandbox_hotels",
            tool_call_id="small",
            arguments={},
            result={"result": "ok"},
            inline_max_bytes=1024,
            page_size=5,
        )
    )
    large, large_reference = asyncio.run(
        persist_tool_result(
            store,
            user_id="user",
            session_id="session",
            tool_name="generic_tool",
            tool_call_id="large",
            arguments={},
            result={"payload": "x" * 2048},
            inline_max_bytes=1024,
            page_size=5,
        )
    )

    assert json.loads(small) == {"result": "ok"}
    assert small_reference is None
    assert json.loads(large)["artifact_id"] == large_reference["artifact_id"]
