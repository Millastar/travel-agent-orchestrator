"""Long-term memory helpers."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def memory_namespace(user_id: str) -> tuple[str, str]:
    return ("memories", user_id)


async def read_long_term_memory(user_id: str, store: Any) -> str:
    """Read only items whose namespace exactly matches the requested user."""
    namespace = memory_namespace(user_id)
    items = await store.asearch(namespace, query="")
    values: list[str] = []
    for item in items or []:
        if tuple(getattr(item, "namespace", ())) != namespace:
            logger.warning("Ignored non-exact long-term memory namespace for user=%s", user_id)
            continue
        value = getattr(item, "value", None)
        if isinstance(value, dict) and isinstance(value.get("data"), str):
            values.append(value["data"])
    logger.info("Loaded long-term memory user=%s items=%d", user_id, len(values))
    return " ".join(values)
