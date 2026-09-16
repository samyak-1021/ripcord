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
from httpx import ASGITransport, AsyncClient
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


# --- Every route, derived from the app rather than listed by hand -------------
#
# The claim is "401 on every protected route", and a hand-maintained list can
# only assert it about the routes someone remembered to add — two had already
# drifted off this one. So the routes now come from the app's own OpenAPI
# schema, and what is written down is the *public* set. A new route is covered
# the moment it exists, and making one public becomes a deliberate edit here
# rather than an omission somewhere else.

# Paths reachable without a credential, and why each is allowed to be.
PUBLIC_PATHS = {
    "/health": "liveness probes run before any credential is provisioned",
    "/metrics": "scraped by Prometheus inside the deployment's network",
    "/docs": "interactive API docs",
    "/redoc": "interactive API docs",
    "/openapi.json": "the schema the docs pages render",
}

# The scope each protected route requires. Every route in the schema must appear
# in exactly one of these two tables — see the completeness test below.
REQUIRED_SCOPE = {
    ("GET", "/flags"): auth.SCOPE_FLAGS_READ,
    ("POST", "/flags"): auth.SCOPE_FLAGS_WRITE,
    ("GET", "/flags/{key}"): auth.SCOPE_FLAGS_READ,
    ("PATCH", "/flags/{key}"): auth.SCOPE_FLAGS_WRITE,
    ("DELETE", "/flags/{key}"): auth.SCOPE_FLAGS_WRITE,
    ("POST", "/evaluate"): auth.SCOPE_SDK,
    ("GET", "/ruleset"): auth.SCOPE_SDK,
    # `sdk`, not `flags:read` — the stream exists for SDK clients to follow
    # invalidations. Getting this wrong in the table below is what the
    # declared-scope test guards against.
    ("GET", "/stream"): auth.SCOPE_SDK,
    ("GET", "/audit"): auth.SCOPE_FLAGS_READ,
    ("GET", "/stats"): auth.SCOPE_FLAGS_READ,
    ("GET", "/keys"): auth.SCOPE_ADMIN,
    ("POST", "/keys"): auth.SCOPE_ADMIN,
    ("DELETE", "/keys/{key_id}"): auth.SCOPE_ADMIN,
}


def _schema_routes(app) -> list[tuple[str, str]]:
    """Every (METHOD, path) the app publishes, from its OpenAPI schema."""
    schema = app.openapi()
    return sorted(
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}
    )


def _concrete(path: str) -> str:
    """Substitute something harmless for each path parameter."""
    return path.replace("{key}", "anything").replace("{key_id}", "deadbeefcafe")


# Routes that hold the connection open once the request is authorised. A
# rejection still returns immediately, so they are safe to probe with a
# credential that should be refused — and must be skipped by any loop using a
# credential that will be accepted, which would otherwise never return.
STREAMING_PATHS = {"/stream"}


def test_every_route_is_classified_as_public_or_scoped(app) -> None:
    """The guard on the two tables above.

    Without it, adding a route and forgetting to list it would silently mean
    "not asserted" rather than "asserted and failing".
    """
    classified = set(REQUIRED_SCOPE) | {
        (method, path) for method, path in _schema_routes(app) if path in PUBLIC_PATHS
    }
    unclassified = set(_schema_routes(app)) - classified
    assert not unclassified, (
        "route(s) neither declared public nor given a required scope: "
        f"{sorted(unclassified)}"
    )


async def test_protected_routes_reject_anonymous(
    app, anon_client: AsyncClient
) -> None:
    """Anonymous access to anything not on the public list must be a 401."""
    failures = []
    for method, path in _schema_routes(app):
        if path in PUBLIC_PATHS:
            continue
        response = await anon_client.request(method, _concrete(path), json={})
        if response.status_code != 401:
            failures.append(f"{method} {path} -> {response.status_code}")
        elif "www-authenticate" not in response.headers:
            failures.append(f"{method} {path} -> 401 without WWW-Authenticate")
    assert not failures, "unprotected route(s): " + ", ".join(failures)


async def test_public_routes_are_reachable_anonymously(
    app, anon_client: AsyncClient
) -> None:
    """The other direction, because the public list is itself a claim.

    `/docs`, `/redoc` and `/openapi.json` are public: a defensible choice for a
    demo service and a questionable one for a private deployment. Writing it
    down keeps it a choice rather than an oversight.
    """
    for path in PUBLIC_PATHS:
        response = await anon_client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


@pytest.mark.parametrize(
    "held_scope", [auth.SCOPE_FLAGS_READ, auth.SCOPE_FLAGS_WRITE, auth.SCOPE_SDK]
)
async def test_every_route_rejects_every_insufficient_scope(
    app, make_key, held_scope: str
) -> None:
    """The full (route x scope) matrix, not four hand-picked cases.

    A key holding exactly one scope is tried against every route. Any route not
    satisfied by that scope must answer 403 — never 200, and never 401, because
    the credential is valid and conflating the two hides real bugs.
    """
    key = await make_key(held_scope)
    principal = auth.Principal(name="probe", scopes=frozenset({held_scope}))

    failures = []
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as caller:
        for (method, path), required in REQUIRED_SCOPE.items():
            if principal.has(required):
                continue  # allowed; the happy paths are covered elsewhere
            response = await caller.request(method, _concrete(path), json={})
            if response.status_code != 403:
                failures.append(
                    f"{method} {path} requires {required}, "
                    f"{held_scope} key got {response.status_code}"
                )

    assert not failures, "scope not enforced: " + "; ".join(failures)


async def test_the_declared_scope_is_the_scope_that_actually_works(
    app, make_key
) -> None:
    """Validate REQUIRED_SCOPE against the app, not just against itself.

    The completeness test proves every route is listed. It cannot prove the
    listing is *right* — and it wasn't: `/stream` was recorded as needing
    `flags:read` when it actually needs `sdk`, which only surfaced because an
    `sdk` key was authorised and the SSE connection hung the suite instead of
    returning 403.

    So each route is called with a key holding exactly its declared scope. A 403
    there means the declaration is wrong.
    """
    wrong = []
    for (method, path), required in REQUIRED_SCOPE.items():
        if path in STREAMING_PATHS:
            continue  # an accepted SSE request never completes
        key = await make_key(required)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {key}"},
        ) as caller:
            response = await caller.request(method, _concrete(path), json={})
        if response.status_code == 403:
            wrong.append(f"{method} {path} is declared as {required} but refused it")

    assert not wrong, "; ".join(wrong)


async def test_an_admin_key_is_never_refused_for_scope(app, make_key) -> None:
    """The complement: admin implies every scope, so 403 must never appear."""
    key = await make_key(auth.SCOPE_ADMIN)

    forbidden = []
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as caller:
        for method, path in REQUIRED_SCOPE:
            if path in STREAMING_PATHS:
                continue  # an accepted SSE request never completes
            response = await caller.request(method, _concrete(path), json={})
            if response.status_code == 403:
                forbidden.append(f"{method} {path}")

    assert not forbidden, "admin was refused on: " + ", ".join(forbidden)


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


# --- The verification cache ---------------------------------------------------
#
# /evaluate answers from Redis, but every request still paid an indexed Postgres
# lookup to verify its key, so "no database on the hot path" was only true of
# the flag. Verified keys are now held in process for a few seconds. That buys
# a round trip and costs revocation latency across processes, and both halves
# are asserted here — a cache whose downside is untested is a liability.


async def test_a_verified_key_is_not_re_read_from_the_database(
    app, make_key, monkeypatch
) -> None:
    """The point of the cache: the second request does no SELECT."""
    from ripcord import auth
    from ripcord.config import settings

    monkeypatch.setattr(settings, "auth_cache_seconds", 5.0)
    auth.invalidate_key_cache()

    key = await make_key("flags:read")
    lookups = 0
    original = auth.select

    def counting_select(*args, **kwargs):
        nonlocal lookups
        lookups += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(auth, "select", counting_select)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as caller:
        assert (await caller.get("/flags")).status_code == 200
        assert lookups == 1, "the first request must read the key from Postgres"
        assert (await caller.get("/flags")).status_code == 200
        assert (await caller.get("/flags")).status_code == 200
        assert lookups == 1, "later requests must be served from the cache"


async def test_a_cached_key_still_rejects_the_wrong_secret(
    app, make_key, monkeypatch
) -> None:
    """A cache hit is the same constant-time digest compare, not a free pass."""
    from ripcord import auth
    from ripcord.config import settings

    monkeypatch.setattr(settings, "auth_cache_seconds", 5.0)
    auth.invalidate_key_cache()

    key = await make_key("flags:read")
    key_id = key.split("_")[1]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as caller:
        warm = await caller.get("/flags", headers={"Authorization": f"Bearer {key}"})
        assert warm.status_code == 200  # the key is now cached

        forged = await caller.get(
            "/flags",
            headers={"Authorization": f"Bearer rpc_{key_id}_not-the-real-secret"},
        )
        assert forged.status_code == 401


async def test_revoking_a_key_takes_effect_immediately_on_this_process(
    app, make_key, monkeypatch
) -> None:
    """The cost of the cache, bounded where it matters most.

    Revocation is the security-critical operation. Within the process that
    handled it there must be no window at all, so the route clears the entry
    rather than waiting for the TTL.
    """
    from ripcord import auth
    from ripcord.config import settings

    monkeypatch.setattr(settings, "auth_cache_seconds", 300.0)  # long on purpose
    auth.invalidate_key_cache()

    admin = await make_key("admin")
    victim = await make_key("flags:read")
    victim_id = victim.split("_")[1]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as caller:
        warm = await caller.get(
            "/flags", headers={"Authorization": f"Bearer {victim}"}
        )
        assert warm.status_code == 200  # cached with a 300s TTL

        revoked = await caller.delete(
            f"/keys/{victim_id}", headers={"Authorization": f"Bearer {admin}"}
        )
        assert revoked.status_code in (200, 204)

        after = await caller.get(
            "/flags", headers={"Authorization": f"Bearer {victim}"}
        )
        assert after.status_code == 401, (
            "a revoked key kept working — the cache was not invalidated"
        )


async def test_a_cached_entry_expires(app, make_key, monkeypatch) -> None:
    """The TTL is the backstop for revocations this process never saw."""
    from ripcord import auth
    from ripcord.config import settings

    monkeypatch.setattr(settings, "auth_cache_seconds", 5.0)
    auth.invalidate_key_cache()

    key = await make_key("flags:read")
    key_id = key.split("_")[1]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as caller:
        await caller.get("/flags")

    assert auth._cached(key_id) is not None

    # Jump past the expiry rather than sleeping through it.
    real_monotonic = auth.time.monotonic
    monkeypatch.setattr(
        auth.time, "monotonic", lambda: real_monotonic() + 3600
    )
    assert auth._cached(key_id) is None, "the entry outlived its TTL"


async def test_the_cache_can_be_switched_off(app, make_key, monkeypatch) -> None:
    """Zero means zero: every request re-reads the key."""
    from ripcord import auth
    from ripcord.config import settings

    monkeypatch.setattr(settings, "auth_cache_seconds", 0.0)
    auth.invalidate_key_cache()

    key = await make_key("flags:read")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as caller:
        await caller.get("/flags")

    assert auth._cached(key.split("_")[1]) is None


# --- Configuration that fails silently is worse than configuration that fails --


def test_a_malformed_bootstrap_key_stops_the_app_from_starting(monkeypatch) -> None:
    """The shipped compose file had a non-hex key_id, and nothing said so.

    `authenticate` rejects an unparseable key before it ever reaches the
    constant-time comparison, so the bootstrap credential simply never matched:
    `docker compose up` gave a stack where every route but /health answered 401
    and no log line explained why. I got the replacement wrong on the first
    attempt too, which is the argument for checking it here rather than by
    eye.
    """
    from ripcord.config import settings
    from ripcord.main import create_app

    monkeypatch.setattr(
        settings, "bootstrap_admin_key", "rpc_0000devonly00_not-hex-key-id"
    )
    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_KEY is malformed"):
        create_app()


def test_the_shipped_development_bootstrap_key_is_valid() -> None:
    """docker-compose.yml's default has to be a key the service can accept."""
    import re
    from pathlib import Path

    compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
    match = re.search(r"BOOTSTRAP_ADMIN_KEY:.*?:-(rpc_[^}]+)\}", compose)
    assert match, "could not find the compose bootstrap key"
    assert auth.split_key(match.group(1)) is not None, (
        f"docker-compose ships an unusable bootstrap key: {match.group(1)}"
    )


def test_the_sse_credential_is_redacted_from_access_logs() -> None:
    """A credential in a URL is a real cost; leaving it in the log is a choice.

    /stream takes its key on the query string because EventSource cannot set
    headers. uvicorn logs the whole request line, so a dashboard tab was writing
    a live operator key into the access log on every reconnect.
    """
    import logging

    from ripcord.logging_config import RedactQueryCredentials

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/stream?api_key=rpc_5d5a6a7d49f9_SuPerSecret", "1.1", 200),
        None,
    )
    RedactQueryCredentials().filter(record)

    rendered = record.getMessage()
    assert "SuPerSecret" not in rendered
    assert "rpc_5d5a6a7d49f9" not in rendered
    assert "api_key=[redacted]" in rendered


def test_redaction_leaves_ordinary_request_lines_alone() -> None:
    import logging

    from ripcord.logging_config import RedactQueryCredentials

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/flags?prefix=checkout", "1.1", 200),
        None,
    )
    RedactQueryCredentials().filter(record)
    assert "/flags?prefix=checkout" in record.getMessage()
