"""Reusable FastAPI dependencies shared across routers."""

from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ripcord.auth import (
    SCOPE_ADMIN,
    SCOPE_FLAGS_READ,
    SCOPE_FLAGS_WRITE,
    SCOPE_SDK,
)
from ripcord.cache import get_redis
from ripcord.db import get_session

# Typed dependencies. The Annotated form keeps the Depends() call out of the
# function-argument default (cleaner, and avoids flake8-bugbear B008).
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]


def _principal_dep(*scopes: str):
    """Annotated dependency carrying the authenticated caller for ``scopes``."""
    from ripcord.auth import Principal, require

    return Annotated[Principal, Depends(require(*scopes))]


# Typed auth dependencies, named for what they guard. Importing these (rather
# than calling `require(...)` inline in every router) keeps the scope each
# endpoint demands visible in one grep.
ReadDep = _principal_dep(SCOPE_FLAGS_READ)
WriteDep = _principal_dep(SCOPE_FLAGS_WRITE)
SdkDep = _principal_dep(SCOPE_SDK)
AdminDep = _principal_dep(SCOPE_ADMIN)


def _stream_dep():
    """SSE-only dependency: also accepts ``?api_key=``.

    Browsers' EventSource cannot send an Authorization header, so this is the
    one route where a query-string credential is permitted. Kept separate from
    ``SdkDep`` so the exception can never spread to another endpoint by
    accident.
    """
    from ripcord.auth import Principal, require

    return Annotated[Principal, Depends(require(SCOPE_SDK, allow_query=True))]


StreamDep = _stream_dep()
