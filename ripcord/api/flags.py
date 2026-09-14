"""HTTP endpoints for creating, reading, updating, and deleting flags.

Every endpoint here is scope-guarded. Reads need ``flags:read``; anything that
mutates a flag needs ``flags:write``, and the authenticated key's name is
recorded as the audit-log actor — so "who turned this on at 2am?" has an answer.
"""

from fastapi import APIRouter, HTTPException, status

from ripcord import cache, services
from ripcord.deps import ReadDep, RedisDep, SessionDep, WriteDep
from ripcord.schemas import FlagCreate, FlagOut, FlagUpdate

router = APIRouter(prefix="/flags", tags=["flags"])


@router.post("", response_model=FlagOut, status_code=status.HTTP_201_CREATED)
async def create_flag(
    payload: FlagCreate, principal: WriteDep, session: SessionDep, redis: RedisDep
) -> FlagOut:
    """Create a new flag, then broadcast the change to connected clients."""
    try:
        flag = await services.create_flag(session, payload, actor=principal.name)
    except services.DuplicateFlagError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Flag '{payload.key}' already exists",
        ) from None
    await cache.notify_flag_change(
        redis, flag.key, "created", services.flag_payload(flag)
    )
    return flag


@router.get("", response_model=list[FlagOut])
async def list_flags(principal: ReadDep, session: SessionDep) -> list[FlagOut]:
    """List all flags."""
    return await services.list_flags(session)


@router.get("/{key}", response_model=FlagOut)
async def get_flag(key: str, principal: ReadDep, session: SessionDep) -> FlagOut:
    """Fetch a single flag by key."""
    flag = await services.get_flag(session, key)
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found")
    return flag


@router.patch("/{key}", response_model=FlagOut)
async def update_flag(
    key: str,
    payload: FlagUpdate,
    principal: WriteDep,
    session: SessionDep,
    redis: RedisDep,
) -> FlagOut:
    """Apply a partial, version-checked update, then broadcast the change."""
    try:
        flag = await services.update_flag(
            session, key, payload, actor=principal.name
        )
    except services.FlagNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found"
        ) from None
    except services.InvalidVariantError as exc:
        # 422: the request is well-formed but semantically invalid — the rule
        # references a variant this flag doesn't define.
        raise HTTPException(422, str(exc)) from None
    except services.VersionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    await cache.notify_flag_change(
        redis, key, "updated", services.flag_payload(flag)
    )
    return flag


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flag(
    key: str, principal: WriteDep, session: SessionDep, redis: RedisDep
) -> None:
    """Delete a flag, then broadcast the change."""
    try:
        deleted = await services.delete_flag(session, key, actor=principal.name)
    except services.VersionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Flag '{key}' not found")
    await cache.notify_flag_change(redis, key, "deleted")
