"""Ripcord API entrypoint: builds the FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ripcord import __version__, metrics
from ripcord.api.evaluate import router as evaluate_router
from ripcord.api.flags import router as flags_router
from ripcord.api.health import router as health_router
from ripcord.api.insights import router as insights_router
from ripcord.api.keys import router as keys_router
from ripcord.api.realtime import router as realtime_router
from ripcord.config import settings
from ripcord.logging_config import configure_logging, log


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Using a factory (rather than a module-level singleton only) keeps the app
    trivial to build fresh inside tests, and gives us one obvious place to
    mount new feature routers as the project grows.
    """
    configure_logging()

    if not settings.auth_enabled:
        # Loud on purpose. An open write API on a flag service means anyone who
        # can reach the port can kill a feature in production.
        log.warning(
            "auth.disabled",
            detail="AUTH_ENABLED=false - the management API is unauthenticated",
        )

    # A bootstrap key that cannot be parsed is rejected inside `authenticate`
    # long before it is ever compared, so a malformed one does not warn — it
    # just silently authenticates nobody. The shipped docker-compose default had
    # a non-hex key_id for exactly this reason: nothing anywhere said so, and
    # `docker compose up` produced a stack where every route but /health
    # returned 401. Failing at startup turns a confusing runtime symptom into an
    # obvious configuration error.
    if settings.bootstrap_admin_key:
        from ripcord.auth import split_key

        if split_key(settings.bootstrap_admin_key) is None:
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_KEY is malformed and would authenticate nobody. "
                "Expected rpc_<12 hex chars>_<secret>; generate one with "
                "`python -m ripcord.cli mint-bootstrap`."
            )

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
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Feature routers are registered here.
    app.include_router(health_router)
    app.include_router(flags_router)
    app.include_router(evaluate_router)
    app.include_router(realtime_router)
    app.include_router(insights_router)
    app.include_router(keys_router)

    return app


# The ASGI application object uvicorn serves: `uvicorn ripcord.main:app`.
app = create_app()
