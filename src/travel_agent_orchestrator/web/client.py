"""Typed HTTP client used by the server-hosted Gradio workspace."""

from __future__ import annotations

from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx


class ApiClientError(RuntimeError):
    """A user-safe API or network failure."""


class AgentApiClient:
    """Call the public FastAPI contract without coupling the UI to service internals."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def __enter__(self) -> AgentApiClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @staticmethod
    def _segment(value: str) -> str:
        return quote(value, safe="")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiClientError("服务响应超时，请稍后重试或检查后台任务状态。") from exc
        except httpx.HTTPError as exc:
            raise ApiClientError("无法连接智能体服务，请确认 API 已启动并可访问。") from exc

        if response.is_success:
            try:
                payload = response.json()
            except ValueError as exc:
                raise ApiClientError("服务返回了无法解析的数据，请检查后端日志。") from exc
            if not isinstance(payload, dict):
                raise ApiClientError("服务返回的数据格式不符合预期。")
            return payload

        try:
            error_payload = response.json()
            detail = error_payload.get("detail") or error_payload.get("message")
        except (ValueError, AttributeError):
            detail = None
        raise ApiClientError(str(detail or f"服务请求失败（HTTP {response.status_code}）。"))

    def close(self) -> None:
        self._client.close()

    def get_system_info(self) -> dict[str, Any]:
        return self._request("GET", "/system/info")

    def get_metrics(self) -> dict[str, Any]:
        return self._request("GET", "/system/metrics")

    def get_latest_evaluation(self) -> dict[str, Any]:
        return self._request("GET", "/evaluation/latest")

    def invoke(
        self,
        user_id: str,
        session_id: str,
        task_id: str,
        query: str,
        system_message: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/agent/invoke",
            json={
                "user_id": user_id,
                "session_id": session_id,
                "task_id": task_id,
                "query": query,
                "system_message": system_message,
            },
        )

    def resume(
        self,
        user_id: str,
        session_id: str,
        task_id: str,
        response_type: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/agent/resume",
            json={
                "user_id": user_id,
                "session_id": session_id,
                "task_id": task_id,
                "response_type": response_type,
                "args": args,
            },
        )

    def get_status(self, user_id: str, session_id: str, task_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/agent/status/"
            f"{self._segment(user_id)}/{self._segment(session_id)}/{self._segment(task_id)}",
        )

    def get_trace(self, user_id: str, session_id: str, task_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/agent/trace/"
            f"{self._segment(user_id)}/{self._segment(session_id)}/{self._segment(task_id)}",
        )

    def get_orders(self, user_id: str) -> dict[str, Any]:
        return self._request("GET", f"/travel/orders/{self._segment(user_id)}")

    def get_order(self, user_id: str, order_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/travel/orders/{self._segment(user_id)}/{self._segment(order_id)}",
        )

    def get_active_session(self, user_id: str) -> dict[str, Any]:
        return self._request("GET", f"/agent/active/sessionid/{self._segment(user_id)}")

    def get_sessions(self, user_id: str) -> dict[str, Any]:
        return self._request("GET", f"/agent/sessionids/{self._segment(user_id)}")

    def get_tasks(self, user_id: str, session_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/agent/tasks/{self._segment(user_id)}/{self._segment(session_id)}"
        )

    def write_memory(self, user_id: str, memory_info: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/agent/write/longterm",
            json={"user_id": user_id, "memory_info": memory_info},
        )

    def delete_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        return self._request(
            "DELETE", f"/agent/session/{self._segment(user_id)}/{self._segment(session_id)}"
        )
