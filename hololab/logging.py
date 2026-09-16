"""Structured logging setup.

Uses structlog for structured events with a human-friendly console renderer in
dev mode. In frozen artifacts we may switch to JSON output; that's a
distribution concern, not a code concern — call ``configure`` again.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure(level: str = "INFO", *, json: bool = False) -> None:
    """Configure structlog and the stdlib logger with sensible defaults."""

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level.upper(),
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger bound with ``component=<name>``."""

    return structlog.get_logger().bind(component=name)
