"""Public request and response contracts."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_SYSTEM_MESSAGE = "你会使用工具来帮助用户。如果工具使用被拒绝，请提示用户。"


class SessionStatus(StrEnum):
    NOT_FOUND = "not_found"
    IDLE = "idle"
    PENDING = "pending"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    ERROR = "error"


class InterruptDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    RESPONSE = "response"


class AgentRequest(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    system_message: str | None = DEFAULT_SYSTEM_MESSAGE


class LongMemRequest(BaseModel):
    user_id: str = Field(min_length=1)
    memory_info: str = Field(min_length=1)


class AgentResponse(BaseModel):
    session_id: str
    task_id: str
    status: str
    timestamp: float = Field(default_factory=time.time)
    message: str | None = None
    result: dict[str, Any] | None = None
    interrupt_data: dict[str, Any] | None = None


class InterruptResponse(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    response_type: str
    args: dict[str, Any] | None = None


class SystemInfoResponse(BaseModel):
    sessions_count: int
    active_users: dict[str, Any] | None = None


class SessionInfoResponse(BaseModel):
    session_ids: list[str]


class TaskInfoResponse(BaseModel):
    task_ids: list[str]


class ActiveSessionInfoResponse(BaseModel):
    active_session_id: str


class SessionStatusResponse(BaseModel):
    user_id: str
    session_id: str | None = None
    task_id: str
    status: str
    message: str | None = None
    last_query: str | None = None
    last_updated: float | None = None
    last_response: AgentResponse | None = None
