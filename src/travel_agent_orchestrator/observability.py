"""Local execution traces with conservative redaction."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from travel_agent_orchestrator.travel.repository import TravelRepository

SENSITIVE_NAMES = {"api_key", "token", "password", "card", "secret", "authorization"}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?<!\d)\d{13,19}(?!\d)"),
)


def redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if any(name in str(key).lower() for name in SENSITIVE_NAMES)
            else redact(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        sanitized = value
        for pattern in SECRET_PATTERNS:
            sanitized = pattern.sub("[redacted]", sanitized)
        return sanitized[:500] + ("…" if len(sanitized) > 500 else "")
    return value


async def record_trace(
    repository: TravelRepository,
    state: dict[str, Any],
    *,
    event_type: str,
    actor: str,
    status: str = "success",
    duration_ms: float | None = None,
    token_usage: int | None = None,
    input_summary: str | None = None,
    output_summary: str | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Append an ordered, user-scoped event and return the next sequence value."""
    sequence = int(state.get("trace_sequence") or 0) + 1
    await repository.append_trace(
        {
            "trace_id": state["trace_id"],
            "user_id": state["user_id"],
            "session_id": state["session_id"],
            "task_id": state["task_id"],
            "sequence": sequence,
            "event_type": event_type,
            "actor": actor,
            "status": status,
            "duration_ms": duration_ms,
            "token_usage": token_usage,
            "input_summary": redact(input_summary),
            "output_summary": redact(output_summary),
            "error_code": error_code,
            "metadata": redact(metadata or {}),
            "timestamp": datetime.now(UTC),
        }
    )
    return sequence
