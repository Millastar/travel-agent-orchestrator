"""PostgreSQL connection and LangGraph schema bootstrap utilities."""

from __future__ import annotations

import asyncio
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from travel_agent_orchestrator.compat import configure_event_loop_policy
from travel_agent_orchestrator.infrastructure.config import Settings, get_settings
from travel_agent_orchestrator.infrastructure.logging import configure_logging
from travel_agent_orchestrator.travel.schema import DOMAIN_SCHEMA_SQL, DOMAIN_SCHEMA_VERSION
from travel_agent_orchestrator.travel.seed import seed_hotel_catalog

logger = logging.getLogger(__name__)


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Create a closed pool so callers control which event loop opens it."""
    return AsyncConnectionPool(
        conninfo=settings.db_uri,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )


async def initialize_database(settings: Settings) -> None:
    """Create LangGraph persistence and versioned hotel sandbox tables."""
    pool = create_pool(settings)
    await pool.open()
    try:
        await AsyncPostgresSaver(pool).setup()
        await AsyncPostgresStore(pool).setup()
        async with pool.connection() as connection:
            for statement in DOMAIN_SCHEMA_SQL.split(";"):
                if statement.strip():
                    await connection.execute(statement)
            await connection.execute(
                """
                INSERT INTO travel_schema_migrations (version)
                VALUES (%s) ON CONFLICT (version) DO NOTHING
                """,
                (DOMAIN_SCHEMA_VERSION,),
            )
        await seed_hotel_catalog(pool)
        logger.info(
            "PostgreSQL schemas and hotel sandbox are ready version=%d",
            DOMAIN_SCHEMA_VERSION,
        )
    finally:
        await pool.close()


def main() -> None:
    configure_event_loop_policy()
    settings = get_settings()
    configure_logging(settings)
    asyncio.run(initialize_database(settings))


if __name__ == "__main__":
    main()
