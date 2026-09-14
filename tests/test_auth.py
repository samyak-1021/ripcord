"""Tests for API-key authentication and scope enforcement.

These cover the three things that actually matter for an auth layer:
  1. Unauthenticated requests are rejected (401) on every protected route.
  2. A valid key with the *wrong* scope is rejected (403) — authentication and
     authorization are separate failures and must not be conflated.
  3. Revocation takes effect immediately.

Plus the properties of the key primitives themselves (secrets are never stored,
keys are unique, malformed input is rejected rather than raising).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ripcord import auth
from ripcord.models import ApiKey

# --- Key primitives (no I/O) -------------------------------------------------


def test_generated_keys_are_unique_and_well_formed() -> None:
    keys = {auth.generate_key()[0] for _ in range(100)}
    assert len(keys) == 100
    for key in keys:
        parts = auth.split_key(key)
        assert parts is not None
        key_id, secret = parts
        assert key_id and secret
        assert key.startswith("rpc_")


def test_generated_key_hash_matches_its_own_secret() -> None:
    full_key, key_id, secret_hash = auth.generate_key()
    parsed_id, secret = auth.split_key(full_key)
    assert parsed_id == key_id
    assert auth.hash_secret(secret) == secret_hash


@pytest.mark.parametrize(
    "bad",
    ["", "nonsense", "rpc_only-two", "xyz_abc_def", "rpc__secret", "rpc_id_"],
)
def test_malformed_keys_are_rejected_not_raised(bad: str) -> None:
    """Garbage input must return None, never blow up inside the auth path."""
    assert auth.split_key(bad) is None


def test_admin_scope_implies_every_other_scope() -> None:
    admin = auth.Principal(name="a", scopes=frozenset({auth.SCOPE_ADMIN}))
    for scope in auth.ALL_SCOPES:
        assert admin.has(scope)


def test_narrow_scope_implies_nothing_else() -> None:
    sdk = auth.Principal(name="s", scopes=frozenset({auth.SCOPE_SDK}))
    assert sdk.has(auth.SCOPE_SDK)
    assert not sdk.has(auth.SCOPE_FLAGS_WRITE)
    assert not sdk.has(auth.SCOPE_ADMIN)


# --- Storage -----------------------------------------------------------------


async def test_secret_is_never_stored(session: AsyncSession, make_key) -> None:
    """The plaintext secret must not be recoverable from the database."""
    full_key = await make_key(auth.SCOPE_SDK)
    _, secret = auth.split_key(full_key)

    row = (await session.execute(select(ApiKey))).scalars().one()
    assert secret not in row.secret_hash
    assert row.secret_hash == auth.hash_secret(secret)
    # Nothing on the row should contain the secret in any column.
    assert secret not in f"{row.key_id}{row.name}{row.scopes}"


# --- 401: no credential ------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/flags"),
        ("POST", "/flags"),
        ("GET", "/flags/anything"),
        ("PATCH", "/flags/anything"),
        ("DELETE", "/flags/anything"),
        ("POST", "/evaluate"),
        ("GET", "/ruleset"),
        ("GET", "/audit"),
        ("GET", "/stats"),
        ("GET", "/keys"),
        ("POST", "/keys"),
    ],
)
async def test_protected_routes_reject_anonymous(
    anon_client: AsyncClient, method: str, path: str
) -> None:
    response = await anon_client.request(method, path, json={})
    assert response.status_code == 401, f"{method} {path} was not protected"
    assert "www-authenticate" in response.headers


async def test_health_stays_public(anon_client: AsyncClient) -> None:
    """Liveness must not need a credential — probes don't carry one."""
    assert (await anon_client.get("/health")).status_code == 200


async def test_invalid_key_is_rejected(anon_client: AsyncClient) -> None:
    anon_client.headers["Authorization"] = "Bearer rpc_deadbeef_notarealsecret"
    assert (await anon_client.get("/flags")).status_code == 401


async def test_x_api_key_header_is_accepted(
    anon_client: AsyncClient, make_key
) -> None:
    """Both header styles work — some proxies strip Authorization."""
    key = await make_key(auth.SCOPE_FLAGS_READ)
    anon_client.headers["X-API-Key"] = key
    assert (await anon_client.get("/flags")).status_code == 200


# --- 403: wrong scope --------------------------------------------------------


async def test_read_key_cannot_write(anon_client: AsyncClient, make_key) -> None:
    """The core property: a read credential cannot change a flag."""
    key = await make_key(auth.SCOPE_FLAGS_READ)
    anon_client.headers["Authorization"] = f"Bearer {key}"

    assert (await anon_client.get("/flags")).status_code == 200

    response = await anon_client.post(
        "/flags", json={"key": "nope", "name": "Nope", "enabled": True}
    )
    assert response.status_code == 403
    assert "flags:write" in response.json()["detail"]


async def test_sdk_key_cannot_touch_management_api(
    anon_client: AsyncClient, make_key
) -> None:
    """The key you ship inside your app must not be able to flip a flag."""
    key = await make_key(auth.SCOPE_SDK, name="checkout-service")
    anon_client.headers["Authorization"] = f"Bearer {key}"

    assert (await anon_client.get("/ruleset")).status_code == 200
    assert (await anon_client.get("/flags")).status_code == 403
    assert (
        await anon_client.post("/flags", json={"key": "x", "name": "X"})
    ).status_code == 403


async def test_non_admin_cannot_manage_keys(
    anon_client: AsyncClient, make_key
) -> None:
    """Privilege escalation check: a write key must not be able to mint keys."""
    key = await make_key(auth.SCOPE_FLAGS_WRITE, auth.SCOPE_FLAGS_READ)
    anon_client.headers["Authorization"] = f"Bearer {key}"

    assert (await anon_client.get("/keys")).status_code == 403
    response = await anon_client.post(
        "/keys", json={"name": "escalate", "scopes": ["admin"]}
    )
    assert response.status_code == 403


# --- Revocation --------------------------------------------------------------


async def test_revoked_key_stops_working_immediately(
    client: AsyncClient, anon_client: AsyncClient, make_key
) -> None:
    victim = await make_key(auth.SCOPE_FLAGS_READ, name="to-be-revoked")
    key_id = auth.split_key(victim)[0]

    anon_client.headers["Authorization"] = f"Bearer {victim}"
    assert (await anon_client.get("/flags")).status_code == 200

    # `client` is the admin-authenticated fixture.
    assert (await client.delete(f"/keys/{key_id}")).status_code == 200

    assert (await anon_client.get("/flags")).status_code == 401


async def test_revocation_is_idempotent(client: AsyncClient, make_key) -> None:
    key = await make_key(auth.SCOPE_SDK)
    key_id = auth.split_key(key)[0]
    first = await client.delete(f"/keys/{key_id}")
    second = await client.delete(f"/keys/{key_id}")
    assert first.status_code == second.status_code == 200
    assert first.json()["revoked_at"] == second.json()["revoked_at"]


# --- Key management ----------------------------------------------------------


async def test_created_key_is_returned_once_and_works(
    client: AsyncClient, anon_client: AsyncClient
) -> None:
    response = await client.post(
        "/keys", json={"name": "ci-deploy", "scopes": ["flags:read", "flags:write"]}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith("rpc_")

    # Listing the keys must never leak the secret back.
    listed = (await client.get("/keys")).json()
    assert all("key" not in item for item in listed)

    # And the returned key actually authenticates.
    anon_client.headers["Authorization"] = f"Bearer {body['key']}"
    assert (await anon_client.get("/flags")).status_code == 200


async def test_unknown_scope_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/keys", json={"name": "typo", "scopes": ["flags:wirte"]}
    )
    assert response.status_code == 422


# --- Audit attribution -------------------------------------------------------


async def test_flag_changes_are_attributed_to_the_key(
    anon_client: AsyncClient, make_key
) -> None:
    """The payoff: the audit log names the credential, not 'system'."""
    key = await make_key(
        auth.SCOPE_FLAGS_WRITE, auth.SCOPE_FLAGS_READ, name="release-bot"
    )
    anon_client.headers["Authorization"] = f"Bearer {key}"

    await anon_client.post(
        "/flags", json={"key": "attributed", "name": "Attributed", "enabled": True}
    )
    entries = (await anon_client.get("/audit?flag_key=attributed")).json()
    assert entries
    assert entries[0]["actor"] == "release-bot"


# --- SSE query-param credential (the EventSource exception) ------------------


def _request(query: str = "", headers: list[tuple[bytes, bytes]] | None = None):
    """Build a bare Starlette Request — no server, no stream to hang on."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/stream",
            "query_string": query.encode(),
            "headers": headers or [],
        }
    )


def test_query_credential_is_read_only_when_allowed() -> None:
    """Browsers can't set headers on EventSource, so /stream takes ?api_key=."""
    request = _request(query=f"{auth.QUERY_PARAM}=rpc_abc123def456_secret")
    assert auth._extract_credential(request, allow_query=True) == (
        "rpc_abc123def456_secret"
    )
    # ...and the same request is anonymous to every other route.
    assert auth._extract_credential(request, allow_query=False) is None


def test_header_credential_wins_over_the_query_string() -> None:
    request = _request(
        query=f"{auth.QUERY_PARAM}=rpc_from_query",
        headers=[(b"authorization", b"Bearer rpc_from_header")],
    )
    assert auth._extract_credential(request, allow_query=True) == "rpc_from_header"


async def test_stream_rejects_a_bad_query_param_credential(
    anon_client: AsyncClient,
) -> None:
    """A rejected stream returns immediately, so this is safe to assert over HTTP."""
    response = await anon_client.get(
        f"/stream?{auth.QUERY_PARAM}=rpc_deadbeefcafe_nope"
    )
    assert response.status_code == 401


async def test_query_param_credential_is_rejected_everywhere_else(
    anon_client: AsyncClient, make_key
) -> None:
    """The exception must not leak beyond /stream.

    A credential in a query string ends up in access logs and browser history.
    It is permitted only where EventSource leaves no alternative, so every other
    route must still refuse it.
    """
    key = await make_key(auth.SCOPE_ADMIN)
    for path in ("/flags", "/ruleset", "/stats", "/audit", "/keys"):
        response = await anon_client.get(f"{path}?{auth.QUERY_PARAM}={key}")
        assert response.status_code == 401, f"{path} accepted a query-string key"
