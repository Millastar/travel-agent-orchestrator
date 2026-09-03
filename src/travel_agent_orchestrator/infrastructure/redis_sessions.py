"""Redis-backed multi-user session and task state."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import redis.asyncio as redis
from pydantic import BaseModel

from travel_agent_orchestrator.domain.models import AgentResponse, SessionStatus
from travel_agent_orchestrator.infrastructure.config import Settings

logger = logging.getLogger(__name__)


class RedisSessionManager:
    """Persist session state while preserving the original Redis key contract."""

    def __init__(
        self,
        redis_url: str,
        session_timeout: int,
        task_ttl: int,
        *,
        key_prefix: str = "",
        client: Any | None = None,
    ) -> None:
        self.redis_client = client or redis.from_url(redis_url, decode_responses=True)
        self.session_timeout = session_timeout
        self.task_ttl = task_ttl
        self.key_prefix = f"{key_prefix.strip(':')}:" if key_prefix else ""

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisSessionManager:
        return cls(
            settings.redis_url,
            settings.session_timeout_seconds,
            settings.task_ttl_seconds,
            key_prefix=settings.redis_key_prefix,
        )

    async def ping(self) -> bool:
        return bool(await self.redis_client.ping())

    async def close(self) -> None:
        close = getattr(self.redis_client, "aclose", None) or self.redis_client.close
        await close()

    def _session_key(self, user_id: str, session_id: str, task_id: str) -> str:
        return f"{self.key_prefix}session:{user_id}:{session_id}:{task_id}"

    def _user_key(self, user_id: str) -> str:
        return f"{self.key_prefix}user_sessions:{user_id}"

    def _mapping_key(self, user_id: str, session_id: str) -> str:
        return f"{self.key_prefix}task_mapping:{user_id}:{session_id}"

    def _task_key(self, task_id: str) -> str:
        return f"{self.key_prefix}task:{task_id}"

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    async def create_session(
        self,
        user_id: str,
        task_id: str,
        session_id: str | None = None,
        status: str = SessionStatus.IDLE,
        last_query: str | None = None,
        last_response: AgentResponse | dict[str, Any] | None = None,
        last_updated: float | None = None,
        ttl: int | None = None,
    ) -> str:
        session_id = session_id or str(uuid.uuid4())
        effective_ttl = ttl or self.session_timeout
        data = {
            "session_id": session_id,
            "task_id": task_id,
            "status": str(status),
            "last_response": last_response,
            "last_query": last_query,
            "last_updated": last_updated if last_updated is not None else time.time(),
        }
        await self.redis_client.set(
            self._session_key(user_id, session_id, task_id),
            json.dumps(data, ensure_ascii=False, default=self._json_default),
            ex=effective_ttl,
        )
        await self.redis_client.sadd(self._user_key(user_id), f"{session_id}:{task_id}")
        await self.redis_client.sadd(self._mapping_key(user_id, session_id), task_id)
        await self.redis_client.expire(self._user_key(user_id), effective_ttl)
        await self.redis_client.expire(self._mapping_key(user_id, session_id), effective_ttl)
        logger.info(
            "Created session state user=%s session=%s task=%s", user_id, session_id, task_id
        )
        return session_id

    async def update_session(
        self,
        user_id: str,
        session_id: str,
        task_id: str,
        *,
        status: str | None = None,
        last_query: str | None = None,
        last_response: AgentResponse | dict[str, Any] | None = None,
        last_updated: float | None = None,
        ttl: int | None = None,
        clear_last_response: bool = False,
    ) -> bool:
        key = self._session_key(user_id, session_id, task_id)
        raw = await self.redis_client.get(key)
        if not raw:
            return False
        data = json.loads(raw)
        if status is not None:
            data["status"] = str(status)
        if last_query is not None:
            data["last_query"] = last_query
        if clear_last_response:
            data["last_response"] = None
        elif last_response is not None:
            data["last_response"] = last_response
        if last_updated is not None:
            data["last_updated"] = last_updated
        effective_ttl = ttl or self.session_timeout
        await self.redis_client.set(
            key,
            json.dumps(data, ensure_ascii=False, default=self._json_default),
            ex=effective_ttl,
        )
        await self.redis_client.expire(self._user_key(user_id), effective_ttl)
        await self.redis_client.expire(self._mapping_key(user_id, session_id), effective_ttl)
        return True

    async def user_id_exists(self, user_id: str) -> bool:
        await self.cleanup_user_tasks(user_id)
        return bool(await self.redis_client.exists(self._user_key(user_id)))

    async def session_id_exists(self, user_id: str, session_id: str) -> bool:
        await self.cleanup_user_tasks(user_id)
        return bool(await self.redis_client.exists(self._mapping_key(user_id, session_id)))

    async def session_task_id_exists(self, user_id: str, session_id: str, task_id: str) -> bool:
        return bool(await self.redis_client.exists(self._session_key(user_id, session_id, task_id)))

    async def get_session_by_task(
        self, user_id: str, session_id: str, task_id: str
    ) -> dict[str, Any] | None:
        raw = await self.redis_client.get(self._session_key(user_id, session_id, task_id))
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("last_response") is not None:
            try:
                data["last_response"] = AgentResponse(**data["last_response"])
            except (TypeError, ValueError):
                logger.warning(
                    "Discarding malformed cached response user=%s session=%s task=%s",
                    user_id,
                    session_id,
                    task_id,
                )
                data["last_response"] = None
        return data

    async def get_all_users_session_ids(self) -> dict[str, list[str]]:
        await self.cleanup_all_tasks()
        result: dict[str, list[str]] = {}
        async for key in self.redis_client.scan_iter(f"{self.key_prefix}user_sessions:*"):
            user_id = key.removeprefix(f"{self.key_prefix}user_sessions:")
            members = await self.redis_client.smembers(key)
            session_ids = sorted({member.split(":", 1)[0] for member in members})
            if session_ids:
                result[user_id] = session_ids
        return result

    async def inspect_runtime_targets(self) -> list[dict[str, Any]]:
        """List session/task identifiers without cleaning or mutating Redis state."""
        targets: list[dict[str, Any]] = []
        async for key in self.redis_client.scan_iter(f"{self.key_prefix}user_sessions:*"):
            user_id = key.removeprefix(f"{self.key_prefix}user_sessions:")
            members = await self.redis_client.smembers(key)
            session_ids = sorted({member.split(":", 1)[0] for member in members})
            for session_id in session_ids:
                task_ids = sorted(
                    await self.redis_client.smembers(self._mapping_key(user_id, session_id))
                )
                targets.append(
                    {
                        "user_id": user_id,
                        "session_id": session_id,
                        "task_ids": task_ids,
                    }
                )
        return targets

    async def get_session_count(self) -> int:
        users = await self.get_all_users_session_ids()
        return sum(len(session_ids) for session_ids in users.values())

    async def get_all_session_ids(self, user_id: str) -> list[str]:
        await self.cleanup_user_tasks(user_id)
        members = await self.redis_client.smembers(self._user_key(user_id))
        return sorted({member.split(":", 1)[0] for member in members})

    async def get_user_active_session_id(self, user_id: str) -> str | None:
        await self.cleanup_user_tasks(user_id)
        members = await self.redis_client.smembers(self._user_key(user_id))
        latest: tuple[float, str] | None = None
        for member in members:
            session_id, task_id = member.split(":", 1)
            session = await self.get_session_by_task(user_id, session_id, task_id)
            updated = session.get("last_updated") if session else None
            if isinstance(updated, int | float) and (latest is None or updated > latest[0]):
                latest = (float(updated), session_id)
        return latest[1] if latest else None

    async def get_session_task_ids(self, user_id: str, session_id: str) -> list[str]:
        await self.cleanup_user_tasks(user_id)
        return sorted(await self.redis_client.smembers(self._mapping_key(user_id, session_id)))

    async def set_task_status(
        self,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        payload = {
            "task_id": task_id,
            "status": str(status),
            "result": result,
            "error": error,
            "user_id": user_id,
            "session_id": session_id,
        }
        await self.redis_client.set(
            self._task_key(task_id),
            json.dumps(payload, ensure_ascii=False, default=self._json_default),
            ex=self.task_ttl,
        )
        if user_id and session_id:
            mapping_key = self._mapping_key(user_id, session_id)
            await self.redis_client.sadd(mapping_key, task_id)
            await self.redis_client.expire(mapping_key, self.task_ttl)

    async def get_single_task_status(self, task_id: str) -> dict[str, Any] | None:
        raw = await self.redis_client.get(self._task_key(task_id))
        return json.loads(raw) if raw else None

    async def get_task_status(self, user_id: str, session_id: str) -> list[str]:
        task_ids = await self.get_session_task_ids(user_id, session_id)
        statuses: list[str] = []
        for task_id in task_ids:
            data = await self.get_single_task_status(task_id)
            if data and data.get("status"):
                statuses.append(f"{task_id}:{data['status']}")
        return statuses

    async def cleanup_user_tasks(self, user_id: str) -> None:
        user_key = self._user_key(user_id)
        members = await self.redis_client.smembers(user_key)
        for member in members:
            session_id, task_id = member.split(":", 1)
            if not await self.redis_client.exists(self._session_key(user_id, session_id, task_id)):
                await self.redis_client.srem(user_key, member)
                await self.redis_client.srem(self._mapping_key(user_id, session_id), task_id)
                await self.redis_client.delete(self._task_key(task_id))
        if not await self.redis_client.scard(user_key):
            await self.redis_client.delete(user_key)

    async def cleanup_all_tasks(self) -> None:
        user_ids: list[str] = []
        async for key in self.redis_client.scan_iter(f"{self.key_prefix}user_sessions:*"):
            user_ids.append(key.removeprefix(f"{self.key_prefix}user_sessions:"))
        for user_id in user_ids:
            await self.cleanup_user_tasks(user_id)

    async def delete_session(
        self, user_id: str, session_id: str, task_id: str | None = None
    ) -> bool:
        mapping_key = self._mapping_key(user_id, session_id)
        task_ids = [task_id] if task_id else list(await self.redis_client.smembers(mapping_key))
        deleted = False
        for current_task_id in task_ids:
            deleted_count = await self.redis_client.delete(
                self._session_key(user_id, session_id, current_task_id),
                self._task_key(current_task_id),
            )
            deleted = deleted or bool(deleted_count)
            await self.redis_client.srem(self._user_key(user_id), f"{session_id}:{current_task_id}")
            await self.redis_client.srem(mapping_key, current_task_id)
        if not await self.redis_client.scard(mapping_key):
            await self.redis_client.delete(mapping_key)
        if not await self.redis_client.scard(self._user_key(user_id)):
            await self.redis_client.delete(self._user_key(user_id))
        return deleted
