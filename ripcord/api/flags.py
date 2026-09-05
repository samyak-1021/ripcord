"""HTTP endpoints for creating, reading, updating, and deleting flags."""

from fastapi import APIRouter, HTTPException, status

from ripcord import cache, services
from ripcord.deps import RedisDep, SessionDep
from ripcord.schemas import FlagCreate, FlagOut, FlagUpdate

router = APIRouter(prefix="/flags", tags=["flags"])


@router.post("", response_model=FlagOut, status_code=status.HTTP_201_CREATED)
async def create_flag(
    payload: FlagCreate, session: SessionDep, redis: RedisDep
) -> FlagOut:
    """Create a new flag, then broadcast the change to connected clients."""
    try:
        flag = await services.create_flag(session, payload)
    except services.DuplicateFlagError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Flag '{payload.key}' already exists",
        ) from None
    await cache.notify_flag_change(redis, flag.key, "created")
    return flag


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
async def update_flag(
    key: str, payload: FlagUpdate, session: SessionDep, redis: RedisDep
) -> FlagOut:
    """Apply a partial, version-checked update, then broadcast the change."""
    try:
        flag = await services.update_flag(session, key, payload)
    except services.FlagNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found"
        ) from None
    except services.VersionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    await cache.notify_flag_change(redis, key, "updated")
    return flag


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flag(key: str, session: SessionDep, redis: RedisDep) -> None:
    """Delete a flag, then broadcast the change."""
    deleted = await services.delete_flag(session, key)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found")
    await cache.notify_flag_change(redis, key, "deleted")
