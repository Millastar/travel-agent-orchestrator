"""Run the FastAPI service with Uvicorn."""

from __future__ import annotations

import uvicorn

from travel_agent_orchestrator.compat import configure_event_loop_policy
from travel_agent_orchestrator.infrastructure.config import get_settings


def main() -> None:
    configure_event_loop_policy()
    settings = get_settings()
    uvicorn.run(
        "travel_agent_orchestrator.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
