"""Ripcord API entrypoint: builds the FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ripcord import __version__, metrics
from ripcord.api.evaluate import router as evaluate_router
from ripcord.api.flags import router as flags_router
from ripcord.api.health import router as health_router
from ripcord.api.insights import router as insights_router
from ripcord.api.realtime import router as realtime_router
from ripcord.config import settings
from ripcord.logging_config import configure_logging


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Using a factory (rather than a module-level singleton only) keeps the app
    trivial to build fresh inside tests, and gives us one obvious place to
    mount new feature routers as the project grows.
    """
    configure_logging()

    app = FastAPI(
        title="Ripcord",
        summary="A self-hostable feature-flag & gradual-rollout service.",
        version=__version__,
    )

    # Prometheus request metrics + /metrics endpoint.
    metrics.setup_metrics(app)

    # Allow the browser dashboard (a separate origin) to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Feature routers are registered here.
    app.include_router(health_router)
    app.include_router(flags_router)
    app.include_router(evaluate_router)
    app.include_router(realtime_router)
    app.include_router(insights_router)

    return app


# The ASGI application object uvicorn serves: `uvicorn ripcord.main:app`.
app = create_app()
