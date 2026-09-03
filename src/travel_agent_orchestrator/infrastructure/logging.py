"""Central logging configuration."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from travel_agent_orchestrator.infrastructure.config import Settings

SENSITIVE_QUERY_VALUE = re.compile(r"(?i)([?&](?:key|api_key|access_token|token)=)[^&\s\"']+")


class RedactingFormatter(logging.Formatter):
    """Redact credential-shaped URL query values, including exception text."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", rendered)


def configure_logging(settings: Settings) -> None:
    """Configure console and rotating-file handlers once per process."""
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers.append(
        RotatingFileHandler(
            settings.log_file,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    )
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        handlers=handlers,
        force=True,
    )
    # Third-party request logs can include MCP credentials in query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
