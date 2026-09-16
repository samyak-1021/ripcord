"""Structured (JSON) logging via structlog."""

import logging
import re

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
    install_access_log_redaction()


class RedactQueryCredentials(logging.Filter):
    """Strip ``?api_key=...`` out of uvicorn's access log lines.

    The SSE endpoint accepts its credential on the query string because the
    browser ``EventSource`` API cannot set headers. That is a documented
    trade-off, but it was an unmitigated one: uvicorn logs the full request
    line, so every dashboard tab wrote a live operator key into the access log
    on every reconnect, in plaintext, where log aggregation keeps it forever.

    Redacting here rather than at the aggregator means the secret never leaves
    the process. It is not a substitute for short-lived stream tokens — that is
    the real fix, noted in the README's limitations — but it removes the leak
    that exists today.
    """

    _PATTERN = re.compile(r"(api_key=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                self._PATTERN.sub(r"\1[redacted]", a) if isinstance(a, str) else a
                for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = self._PATTERN.sub(r"\1[redacted]", record.msg)
        return True


def install_access_log_redaction() -> None:
    """Attach the redaction filter to uvicorn's access logger."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactQueryCredentials) for f in access.filters):
        access.addFilter(RedactQueryCredentials())


# Shared logger. structlog binds the active config lazily on first use.
log = structlog.get_logger("ripcord")
