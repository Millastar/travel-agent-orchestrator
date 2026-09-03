"""Tool-result persistence and compact model-facing projections."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from travel_agent_orchestrator.agent.tools import _decode_structured_output


def artifact_namespace(user_id: str, session_id: str) -> tuple[str, str, str]:
    return ("tool_artifacts", user_id, session_id)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    return value


def _nested_value(item: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = item
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                current = None
                break
            current = current[key]
        if current not in (None, "", []):
            return current
    return None


def _poi_projection(item: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    """Expose useful POI evidence while excluding bulky and irrelevant provider fields."""
    projected = {
        "poi_id": _nested_value(item, ("id",), ("poi_id",)),
        "name": _nested_value(item, ("name",)),
        "address": _nested_value(item, ("address",)),
        "distance": _nested_value(item, ("distance",)),
        "location": _nested_value(item, ("location",)),
        "rating": _nested_value(
            item,
            ("rating",),
            ("business", "rating"),
            ("biz_ext", "rating"),
        ),
        "category": _nested_value(item, ("type",), ("category",)),
    }
    if detail:
        projected.update(
            {
                "telephone": _nested_value(item, ("tel",), ("telephone",)),
                "business_area": _nested_value(
                    item,
                    ("business_area",),
                    ("business", "business_area"),
                ),
                "opening_hours": _nested_value(
                    item,
                    ("business", "opentime_today"),
                    ("business", "opentime_week"),
                    ("opening_hours",),
                ),
                "average_cost": _nested_value(
                    item,
                    ("business", "cost"),
                    ("biz_ext", "cost"),
                ),
            }
        )
    return {key: value for key, value in projected.items() if value not in (None, "", [])}


def _items_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("pois"), list):
        return [item for item in payload["pois"] if isinstance(item, dict)]
    return []


def artifact_page_payload(
    artifact_id: str,
    raw_payload: Any,
    *,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    items = _items_from_payload(raw_payload)
    start = max(page - 1, 0) * page_size
    shown = [_poi_projection(item) for item in items[start : start + page_size]]
    return {
        "artifact_id": artifact_id,
        "page": page,
        "page_size": page_size,
        "total": len(items),
        "shown": len(shown),
        "summary": shown,
        "has_more": start + len(shown) < len(items),
        "hint": "如需后续结果，请输入“查看更多”或“下一页”。",
    }


def artifact_detail_payload(
    artifact_id: str,
    raw_payload: Any,
    *,
    poi_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Return a safe detail projection for one POI from the persisted raw result."""
    normalized_id = str(poi_id or "").strip()
    normalized_name = str(name or "").strip()
    items = _items_from_payload(raw_payload)
    selected = next(
        (
            item
            for item in items
            if (normalized_id and str(item.get("id") or item.get("poi_id") or "") == normalized_id)
            or (normalized_name and str(item.get("name") or "") == normalized_name)
        ),
        None,
    )
    if selected is None:
        return {
            "artifact_id": artifact_id,
            "error": "当前工具产物中未找到指定地点。",
        }
    return {
        "artifact_id": artifact_id,
        "detail": _poi_projection(selected, detail=True),
    }


async def persist_tool_result(
    store: Any,
    *,
    user_id: str,
    session_id: str,
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    result: Any,
    inline_max_bytes: int,
    page_size: int,
) -> tuple[str, dict[str, Any] | None]:
    decoded = _decode_structured_output(result)
    normalized = _jsonable(decoded)
    serialized = json.dumps(normalized, ensure_ascii=False, default=str)
    should_store = (
        tool_name.startswith("maps_") or len(serialized.encode("utf-8")) > inline_max_bytes
    )
    if not should_store:
        return serialized if not isinstance(result, str) else result, None

    artifact_id = f"tool-result-{uuid.uuid4()}"
    value = {
        "artifact_id": artifact_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "arguments": _jsonable(arguments),
        "raw_output": normalized,
        "created_at": time.time(),
    }
    await store.aput(
        namespace=artifact_namespace(user_id, session_id),
        key=artifact_id,
        value=value,
    )
    page_payload = artifact_page_payload(
        artifact_id,
        normalized,
        page=1,
        page_size=page_size,
    )
    if not page_payload["summary"]:
        page_payload["preview"] = serialized[:4000]
    reference = {
        "artifact_id": artifact_id,
        "tool_name": tool_name,
        "total": page_payload["total"],
        "shown": page_payload["shown"],
        "last_page": 1,
    }
    return json.dumps(page_payload, ensure_ascii=False), reference


async def read_artifact_page(
    store: Any,
    *,
    user_id: str,
    session_id: str,
    artifact_id: str,
    page: int,
    page_size: int,
    poi_id: str | None = None,
    name: str | None = None,
) -> str:
    item = await store.aget(artifact_namespace(user_id, session_id), artifact_id)
    value = getattr(item, "value", None) if item is not None else None
    if not isinstance(value, dict):
        return json.dumps(
            {"error": "未找到该工具结果，可能已删除或不属于当前会话。"},
            ensure_ascii=False,
        )
    if poi_id or name:
        payload = artifact_detail_payload(
            artifact_id,
            value.get("raw_output"),
            poi_id=poi_id,
            name=name,
        )
    else:
        payload = artifact_page_payload(
            artifact_id,
            value.get("raw_output"),
            page=max(page, 1),
            page_size=page_size,
        )
    return json.dumps(payload, ensure_ascii=False)
