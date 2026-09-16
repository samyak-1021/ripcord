"""Admin endpoints for minting, listing, and revoking API keys.

All of these require the ``admin`` scope — including listing, because the set of
live credentials and their scopes is itself sensitive.

The create endpoint is the only place in the system where a key's secret is ever
returned. There is deliberately no "show key again" endpoint: the secret is not
stored, so it genuinely cannot be recovered, and an endpoint that pretended
otherwise would be lying about the security model.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ripcord import auth
from ripcord.deps import AdminDep, SessionDep
from ripcord.logging_config import log
from ripcord.models import ApiKey
from ripcord.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

router = APIRouter(prefix="/keys", tags=["keys"])


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    payload: ApiKeyCreate, principal: AdminDep, session: SessionDep
) -> ApiKeyCreated:
    """Mint a key. The plaintext secret is in this response and nowhere else."""
    full_key, key_id, secret_hash = auth.generate_key()
    api_key = ApiKey(
        key_id=key_id,
        secret_hash=secret_hash,
        name=payload.name,
        scopes=payload.scopes,
    )
    session.add(api_key)
    await session.commit()
    # The key_id is safe to log; the secret is not, and is never logged.
    log.info(
        "apikey.created",
        key_id=key_id,
        name=payload.name,
        scopes=payload.scopes,
        actor=principal.name,
    )
    return ApiKeyCreated(
        key=full_key,
        key_id=api_key.key_id,
        name=api_key.name,
        scopes=api_key.scopes,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(principal: AdminDep, session: SessionDep) -> list[ApiKey]:
    """List every key, including revoked ones (revocation is a tombstone)."""
    result = await session.execute(select(ApiKey).order_by(ApiKey.id))
    return list(result.scalars().all())


@router.delete("/{key_id}", response_model=ApiKeyOut)
async def revoke_key(
    key_id: str, principal: AdminDep, session: SessionDep
) -> ApiKey:
    """Revoke a key. Idempotent: revoking an already-revoked key is a no-op."""
    result = await session.execute(select(ApiKey).where(ApiKey.key_id == key_id))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Key '{key_id}' not found")
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await session.commit()
        # Revocation is immediate on this process. Other processes fall back to
        # the verification cache's TTL — see auth.py's module docstring.
        auth.invalidate_key_cache(key_id)
        log.info("apikey.revoked", key_id=key_id, actor=principal.name)
    return api_key
