"""Reusable FastAPI dependencies shared across routers."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ripcord.db import get_session

# Typed DB-session dependency. The Annotated form keeps the Depends() call out
# of the function-argument default (cleaner, and avoids flake8-bugbear B008).
SessionDep = Annotated[AsyncSession, Depends(get_session)]
