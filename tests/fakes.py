from __future__ import annotations

from collections.abc import AsyncIterator
from fnmatch import fnmatch
from typing import Any


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def set(self, key: str, value: str, **_: Any) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def exists(self, key: str) -> int:
        return int(key in self.values or bool(self.sets.get(key)))

    async def sadd(self, key: str, *members: str) -> int:
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(members)
        return len(target) - before

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, *members: str) -> int:
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.difference_update(members)
        return before - len(target)

    async def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    async def expire(self, key: str, seconds: int) -> bool:
        return seconds > 0 and (key in self.values or key in self.sets)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
            if key in self.sets:
                del self.sets[key]
                deleted += 1
        return deleted

    async def scan_iter(self, pattern: str) -> AsyncIterator[str]:
        for key in sorted(set(self.values) | set(self.sets)):
            if fnmatch(key, pattern):
                yield key


class FakeTask:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def delay(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class FakeMemoryStore:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def aput(self, **kwargs: Any) -> None:
        self.items.append(kwargs)

    async def asearch(self, namespace, **_: Any):
        return [
            type(
                "FakeStoreItem",
                (),
                {
                    "namespace": tuple(item["namespace"]),
                    "key": item["key"],
                    "value": item["value"],
                },
            )()
            for item in self.items
            if tuple(item["namespace"]) == tuple(namespace)
        ]

    async def aget(self, namespace, key: str):
        return next(
            (item for item in await self.asearch(namespace) if str(item.key) == str(key)),
            None,
        )

    async def adelete(self, namespace, key: str) -> None:
        self.items = [
            item
            for item in self.items
            if not (tuple(item["namespace"]) == tuple(namespace) and str(item["key"]) == str(key))
        ]
