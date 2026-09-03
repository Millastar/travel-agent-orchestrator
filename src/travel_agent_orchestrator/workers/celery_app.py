"""Celery application configuration."""

from __future__ import annotations

from celery import Celery

from travel_agent_orchestrator.compat import configure_event_loop_policy
from travel_agent_orchestrator.infrastructure.config import get_settings

configure_event_loop_policy()
settings = get_settings()

celery_app = Celery(
    "travel_agent_orchestrator",
    broker=settings.celery_broker_url,
    include=["travel_agent_orchestrator.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
)
