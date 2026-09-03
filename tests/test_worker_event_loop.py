from __future__ import annotations

import asyncio

from travel_agent_orchestrator.workers.event_loop import (
    close_worker_event_loop,
    run_worker_coroutine,
)


async def current_loop_id() -> int:
    return id(asyncio.get_running_loop())


def test_worker_reuses_one_event_loop_until_shutdown() -> None:
    try:
        first = run_worker_coroutine(current_loop_id())
        second = run_worker_coroutine(current_loop_id())
    finally:
        close_worker_event_loop()

    assert first == second
