"""API-key authentication and scope-based authorization.

Design notes — the interesting decisions live here:

**Key format.** A key looks like ``rpc_<key_id>_<secret>``. The ``key_id`` is
stored in plaintext and indexed, so verifying a key is a single indexed lookup
rather than a scan-and-hash over every row. The ``secret`` half is never stored;
only its SHA-256 digest is.

**Why SHA-256 and not bcrypt/argon2.** Password hashing is deliberately slow
because human passwords carry maybe 30 bits of entropy and must survive an
offline brute force. These secrets are 256 bits from ``secrets.token_urlsafe``,
so brute force is not on the table and a slow KDF would only add latency to
every single authenticated request. A fast digest with a constant-time compare
is the right tool. (If keys were ever user-chosen, this reasoning inverts.)

**Constant-time comparison.** ``hmac.compare_digest`` avoids leaking how much of
a digest matched via response timing.

**Scopes, not roles.** A key carries an explicit set of scopes. The SDK-facing
read path (``sdk``) is separated from the management write path (``flags:write``)
so the credential you ship inside your application binary cannot flip a flag —
which is the entire point of having auth on a flag service at all.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ripcord.config import settings
from ripcord.db import get_session
from ripcord.logging_config import log
from ripcord.models import ApiKey

# --- Scopes ----------------------------------------------------------------

SCOPE_FLAGS_READ = "flags:read"
SCOPE_FLAGS_WRITE = "flags:write"
SCOPE_SDK = "sdk"
SCOPE_ADMIN = "admin"

ALL_SCOPES: frozenset[str] = frozenset(
    {SCOPE_FLAGS_READ, SCOPE_FLAGS_WRITE, SCOPE_SDK, SCOPE_ADMIN}
)

# `admin` is a superset: an admin key satisfies every scope check. Keeping this
# as data (rather than an `if principal.is_admin` sprinkled through the routers)
# means the authorization rule lives in exactly one place.
_IMPLIED: dict[str, frozenset[str]] = {SCOPE_ADMIN: ALL_SCOPES}

KEY_PREFIX = "rpc"
# The key_id is hex on purpose: `token_urlsafe` emits '-' and '_', and an
# underscore inside the id would make `rpc_<id>_<secret>` ambiguous to parse.
# Hex has no such characters, so the separator stays unambiguous.
_KEY_ID_BYTES = 6  # -> 12 hex chars
_SECRET_BYTES = 32  # -> 43 url-safe chars, 256 bits of entropy

# Writing `last_used_at` on every request would turn every authenticated read
# into a write. We only refresh it once the stored value is this stale, which
# keeps the column useful for "is this key still in use?" without the cost.
_LAST_USED_REFRESH = timedelta(minutes=5)


@dataclass(frozen=True)
class Principal:
    """Who is making the request, and what they are allowed to do."""

    name: str
    scopes: frozenset[str]
    key_id: str | None = None

    def effective_scopes(self) -> frozenset[str]:
        """Scopes held directly, plus everything those scopes imply."""
        effective = set(self.scopes)
        for scope in self.scopes:
            effective |= _IMPLIED.get(scope, frozenset())
        return frozenset(effective)

    def has(self, scope: str) -> bool:
        return scope in self.effective_scopes()


# The principal used when auth is switched off. Named so it is obvious in the
# audit log that the change was not attributed to a real credential.
ANONYMOUS = Principal(name="anonymous", scopes=ALL_SCOPES)


# --- Key generation & verification ------------------------------------------


def generate_key() -> tuple[str, str, str]:
    """Mint a new key.

    Returns ``(full_key, key_id, secret_hash)``. The full key is the only time
    the secret exists in plaintext anywhere — the caller must show it to the
    user once and then forget it.
    """
    key_id = secrets.token_hex(_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    full_key = f"{KEY_PREFIX}_{key_id}_{secret}"
    return full_key, key_id, hash_secret(secret)


def hash_secret(secret: str) -> str:
    """SHA-256 digest of a key's secret half. See the module docstring."""
    return hashlib.sha256(secret.encode()).hexdigest()


def split_key(full_key: str) -> tuple[str, str] | None:
    """Split ``rpc_<key_id>_<secret>`` into its parts, or None if malformed.

    Split with ``maxsplit=2`` so that underscores inside the url-safe secret
    stay part of the secret. Only the prefix and the hex key_id are delimited.
    """
    parts = full_key.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    _, key_id, secret = parts
    if not key_id or not secret:
        return None
    # A key_id is always hex; anything else is a malformed or forged key.
    if any(c not in "0123456789abcdef" for c in key_id):
        return None
    return key_id, secret


# Query-string name accepted *only* on the SSE endpoint. See below.
QUERY_PARAM = "api_key"


def _extract_credential(request: Request, allow_query: bool = False) -> str | None:
    """Pull the key from ``Authorization: Bearer ...`` or ``X-API-Key``.

    ``allow_query`` additionally accepts ``?api_key=`` and is enabled for
    exactly one route: ``/stream``. The browser ``EventSource`` API cannot set
    request headers, so an SSE endpoint a browser consumes has no other way to
    authenticate. It stays opt-in per route because credentials in a URL leak
    into access logs, browser history and ``Referer`` headers — a real cost,
    accepted here only where the platform leaves no alternative.
    """
    header = request.headers.get("authorization")
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
    from_header = request.headers.get("x-api-key")
    if from_header:
        return from_header
    if allow_query:
        return request.query_params.get(QUERY_PARAM)
    return None


async def _touch_last_used(session: AsyncSession, api_key: ApiKey) -> None:
    """Best-effort refresh of ``last_used_at``; never fails the request."""
    now = datetime.now(UTC)
    if api_key.last_used_at and now - api_key.last_used_at < _LAST_USED_REFRESH:
        return
    try:
        api_key.last_used_at = now
        await session.commit()
    except Exception:  # pragma: no cover - telemetry must not break auth
        await session.rollback()
        log.warning("auth.last_used_update_failed", key_id=api_key.key_id)


async def authenticate(session: AsyncSession, full_key: str) -> Principal | None:
    """Resolve a raw key string to a Principal, or None if it is not valid."""
    parts = split_key(full_key)
    if parts is None:
        return None
    key_id, secret = parts

    # The bootstrap key is checked before the database on purpose: it is what
    # you use to mint the first real key, and to recover if every stored key is
    # revoked. It is compared in constant time like any other credential.
    bootstrap = settings.bootstrap_admin_key
    if bootstrap and hmac.compare_digest(full_key, bootstrap):
        return Principal(name="bootstrap", scopes=frozenset({SCOPE_ADMIN}))

    result = await session.execute(select(ApiKey).where(ApiKey.key_id == key_id))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        # Hash anyway so a wrong key_id and a wrong secret take the same time.
        hash_secret(secret)
        return None
    if api_key.revoked_at is not None:
        return None
    if not hmac.compare_digest(api_key.secret_hash, hash_secret(secret)):
        return None

    await _touch_last_used(session, api_key)
    return Principal(
        name=api_key.name,
        scopes=frozenset(api_key.scopes),
        key_id=api_key.key_id,
    )


# --- FastAPI dependency ------------------------------------------------------


def require(*required: str, allow_query: bool = False):
    """Build a dependency that authenticates and enforces ``required`` scopes.

    Usage::

        @router.post("", dependencies=[Depends(require(SCOPE_FLAGS_WRITE))])

    or, when the handler wants to know who the caller is (for the audit log)::

        async def create_flag(principal: WriteDep, ...):
    """

    async def dependency(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Principal:
        if not settings.auth_enabled:
            return ANONYMOUS

        credential = _extract_credential(request, allow_query=allow_query)
        if not credential:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API key. Send 'Authorization: Bearer <key>'.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        principal = await authenticate(session, credential)
        if principal is None:
            log.warning("auth.rejected", path=request.url.path)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        missing = [s for s in required if not principal.has(s)]
        if missing:
            # 403, not 401: the caller proved who they are, they just aren't
            # allowed to do this. Naming the missing scope is safe and saves a
            # support round-trip.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key lacks required scope(s): {', '.join(missing)}",
            )

        return principal

    return dependency
