"""Persistent asyncio runner for Celery's synchronous Windows worker process."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from celery.signals import worker_shutdown

ResultT = TypeVar("ResultT")
_runner: asyncio.Runner | None = None
_runner_lock = threading.Lock()


def run_worker_coroutine(coroutine: Coroutine[Any, Any, ResultT]) -> ResultT:
    """Run tasks on one process-long event loop so MCP cleanup can finish safely."""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = asyncio.Runner()
        result = _runner.run(coroutine)
        # Give session cleanup callbacks one additional event-loop turn.
        _runner.run(asyncio.sleep(0))
        return result


@worker_shutdown.connect
def close_worker_event_loop(**_: Any) -> None:
    """Close the persistent runner when Celery shuts down."""
    global _runner
    with _runner_lock:
        if _runner is not None:
            _runner.close()
            _runner = None
