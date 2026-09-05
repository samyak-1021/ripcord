"""Structured (JSON) logging via structlog."""

import logging

import structlog


def configure_logging() -> None:
    """Configure structlog to emit one JSON object per log line."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )


# Shared logger. structlog binds the active config lazily on first use.
log = structlog.get_logger("ripcord")
