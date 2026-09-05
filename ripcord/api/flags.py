"""HTTP endpoints for creating, reading, updating, and deleting flags."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ripcord import services
from ripcord.db import get_session
from ripcord.schemas import FlagCreate, FlagOut, FlagUpdate

router = APIRouter(prefix="/flags", tags=["flags"])

# Typed dependency alias — the Annotated form keeps Depends() out of the
# argument default (cleaner, and avoids the flake8-bugbear B008 warning).
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=FlagOut, status_code=status.HTTP_201_CREATED)
async def create_flag(payload: FlagCreate, session: SessionDep) -> FlagOut:
    """Create a new flag."""
    try:
        return await services.create_flag(session, payload)
    except services.DuplicateFlagError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Flag '{payload.key}' already exists",
        ) from None


@router.get("", response_model=list[FlagOut])
async def list_flags(session: SessionDep) -> list[FlagOut]:
    """List all flags."""
    return await services.list_flags(session)


@router.get("/{key}", response_model=FlagOut)
async def get_flag(key: str, session: SessionDep) -> FlagOut:
    """Fetch a single flag by key."""
    flag = await services.get_flag(session, key)
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found")
    return flag


@router.patch("/{key}", response_model=FlagOut)
async def update_flag(key: str, payload: FlagUpdate, session: SessionDep) -> FlagOut:
    """Apply a partial, version-checked update to a flag."""
    try:
        return await services.update_flag(session, key, payload)
    except services.FlagNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found"
        ) from None
    except services.VersionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flag(key: str, session: SessionDep) -> None:
    """Delete a flag."""
    deleted = await services.delete_flag(session, key)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found")
