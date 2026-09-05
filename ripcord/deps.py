"""Reusable FastAPI dependencies shared across routers."""

from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ripcord.cache import get_redis
from ripcord.db import get_session

# Typed dependencies. The Annotated form keeps the Depends() call out of the
# function-argument default (cleaner, and avoids flake8-bugbear B008).
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]
