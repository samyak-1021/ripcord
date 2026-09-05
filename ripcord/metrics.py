"""Prometheus metrics: request count + latency, and a flag-evaluation counter."""

import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Metrics are defined once at import, so they register with the default registry
# exactly once even though create_app() runs per test.
http_requests_total = Counter(
    "ripcord_http_requests_total", "Total HTTP requests", ["method", "status"]
)
http_request_duration_seconds = Histogram(
    "ripcord_http_request_duration_seconds", "HTTP request latency in seconds"
)
flag_evaluations_total = Counter(
    "ripcord_flag_evaluations_total", "Total flag evaluations by result", ["result"]
)


class _MetricsMiddleware:
    """Pure-ASGI middleware recording request count + latency.

    Deliberately *not* a Starlette BaseHTTPMiddleware: a pure-ASGI middleware
    streams responses through untouched, so it never buffers or breaks the SSE
    endpoint.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        status = {"code": 500}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        start = time.perf_counter()
        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            http_requests_total.labels(
                scope.get("method", "GET"), str(status["code"])
            ).inc()
            # Exclude the long-lived SSE stream from the latency histogram.
            if scope.get("path") != "/stream":
                http_request_duration_seconds.observe(time.perf_counter() - start)


def setup_metrics(app: FastAPI) -> None:
    """Attach the metrics middleware and expose the /metrics endpoint."""
    app.add_middleware(_MetricsMiddleware)

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
