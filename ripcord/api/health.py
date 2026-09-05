"""Health-check endpoint used by load balancers, CI, and uptime probes."""

from fastapi import APIRouter
from pydantic import BaseModel

from ripcord import __version__
from ripcord.config import settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Body returned by the health endpoint."""

    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return a simple liveness signal.

    Intentionally dependency-free: it must stay green even if Postgres or
    Redis are down, so orchestrators can tell "process is up" apart from
    "a dependency is degraded". A deeper readiness probe comes later.
    """
    return HealthResponse(status="ok", service=settings.app_name, version=__version__)
