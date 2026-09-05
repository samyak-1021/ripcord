"""Ripcord API entrypoint: builds the FastAPI application."""

from fastapi import FastAPI

from ripcord import __version__
from ripcord.api.flags import router as flags_router
from ripcord.api.health import router as health_router


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Using a factory (rather than a module-level singleton only) keeps the app
    trivial to build fresh inside tests, and gives us one obvious place to
    mount new feature routers as the project grows.
    """
    app = FastAPI(
        title="Ripcord",
        summary="A self-hostable feature-flag & gradual-rollout service.",
        version=__version__,
    )

    # Feature routers are registered here.
    app.include_router(health_router)
    app.include_router(flags_router)

    return app


# The ASGI application object uvicorn serves: `uvicorn ripcord.main:app`.
app = create_app()
