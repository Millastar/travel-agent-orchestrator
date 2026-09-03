"""External MCP tool discovery and safe result decoding."""

from __future__ import annotations

import ast
import json
import logging
from typing import Any

from langchain_core.tools import BaseTool

from travel_agent_orchestrator.infrastructure.config import Settings

logger = logging.getLogger(__name__)


def _decode_structured_output(value: Any, *, depth: int = 0) -> Any:
    """Decode nested MCP text envelopes without evaluating executable input."""
    if depth >= 6:
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return value
        for parser in (json.loads, ast.literal_eval):
            try:
                decoded = parser(candidate)
            except (TypeError, ValueError, SyntaxError, json.JSONDecodeError):
                continue
            if decoded != value:
                return _decode_structured_output(decoded, depth=depth + 1)
        return value
    if isinstance(value, dict):
        if value.get("type") == "text" and "text" in value:
            return _decode_structured_output(value["text"], depth=depth + 1)
        if isinstance(value.get("pois"), list):
            return value
        for key in ("data", "result", "content"):
            if key in value:
                decoded = _decode_structured_output(value[key], depth=depth + 1)
                if isinstance(decoded, dict) and isinstance(decoded.get("pois"), list):
                    return decoded
        return value
    if isinstance(value, list):
        decoded_items = [_decode_structured_output(item, depth=depth + 1) for item in value]
        for item in decoded_items:
            if isinstance(item, dict) and isinstance(item.get("pois"), list):
                return item
        return decoded_items
    return value


async def get_agent_tools(settings: Settings) -> list[BaseTool]:
    """Load AMap's read-only MCP tools when configured; otherwise degrade safely."""
    if not settings.amap_maps_api_key:
        logger.info("AMAP_MAPS_API_KEY is not configured; AMap discovery is disabled")
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "amap-maps": {
                "url": f"https://mcp.amap.com/mcp?key={settings.amap_maps_api_key}",
                "transport": "streamable_http",
            }
        }
    )
    try:
        tools = list(await client.get_tools())
    except Exception:
        logger.exception("AMap MCP tools could not be loaded; using the sandbox catalog")
        return []
    logger.info(
        "Registered AMap MCP tools count=%d names=%s",
        len(tools),
        ",".join(item.name for item in tools),
    )
    return tools
