from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from travel_agent_orchestrator.agent.memory import read_long_term_memory


@dataclass
class MemoryItem:
    namespace: tuple[str, str]
    value: dict[str, str]


class PrefixMatchingStore:
    async def asearch(self, namespace: tuple[str, str], query: str) -> list[Any]:
        assert namespace == ("memories", "user-1")
        assert query == ""
        return [
            MemoryItem(("memories", "user-1"), {"data": "preferred"}),
            MemoryItem(("memories", "user-10"), {"data": "must not leak"}),
        ]


def test_memory_reader_rejects_prefix_namespace_matches() -> None:
    result = asyncio.run(read_long_term_memory("user-1", PrefixMatchingStore()))

    assert result == "preferred"
