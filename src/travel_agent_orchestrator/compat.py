"""Runtime compatibility helpers."""

from __future__ import annotations

import asyncio
import sys


def configure_event_loop_policy() -> None:
    """Use the selector loop required by Psycopg async connections on Windows."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
