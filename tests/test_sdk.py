"""Tests for the Ripcord Python SDK (local evaluation + fail-open + watch)."""

import asyncio

import pytest

from ripcord.sdk import RipcordClient


async def test_sdk_bootstraps_and_evaluates_locally(client):
    """After start(), flags evaluate locally with no further network calls."""
    await client.post(
        "/flags",
        json={"key": "on", "name": "On", "enabled": True, "rollout_percentage": 100},
    )
    await client.post("/flags", json={"key": "off", "name": "Off", "enabled": False})

    sdk = RipcordClient(http_client=client)
    await sdk.start(watch=False)
    try:
        assert sdk.is_enabled("on", "u1") is True
        assert sdk.is_enabled("off", "u1") is False
    finally:
        await sdk.close()


async def test_sdk_unknown_flag_returns_default(client):
    """A flag the SDK hasn't loaded returns the caller's default."""
    sdk = RipcordClient(http_client=client)
    await sdk.start(watch=False)
    try:
        assert sdk.is_enabled("missing", "u1") is False
        assert sdk.is_enabled("missing", "u1", default=True) is True
    finally:
        await sdk.close()


async def test_sdk_evaluates_targeting_rules_locally(client):
    """Targeting rules are applied by the local engine, identical to the server."""
    await client.post(
        "/flags",
        json={
            "key": "beta", "name": "Beta", "enabled": True, "rollout_percentage": 0,
            "rules": [{"attribute": "country", "operator": "in", "values": ["IN"]}],
        },
    )
    sdk = RipcordClient(http_client=client)
    await sdk.start(watch=False)
    try:
        assert sdk.is_enabled("beta", "u1", {"country": "IN"}) is True
        assert sdk.is_enabled("beta", "u1", {"country": "US"}) is False
    finally:
        await sdk.close()


async def test_sdk_refresh_picks_up_changes(client):
    """refresh() pulls the latest ruleset from the server."""
    await client.post("/flags", json={"key": "f", "name": "F", "enabled": False})
    sdk = RipcordClient(http_client=client)
    await sdk.start(watch=False)
    try:
        assert sdk.is_enabled("f", "u1") is False
        await client.patch(
            "/flags/f", json={"enabled": True, "rollout_percentage": 100, "version": 1}
        )
        await sdk.refresh()
        assert sdk.is_enabled("f", "u1") is True
    finally:
        await sdk.close()


async def test_sdk_fails_open_when_server_unreachable():
    """If bootstrap can't reach the server, the SDK serves defaults, not errors."""
    sdk = RipcordClient(base_url="http://127.0.0.1:59999")  # nothing is listening
    await sdk.start(watch=False)
    try:
        assert sdk.is_enabled("anything", "u1") is False
        assert sdk.is_enabled("anything", "u1", default=True) is True
    finally:
        await sdk.close()


# --- watch loop: a fake HTTP client lets us drive the SSE stream deterministically
# (no real server, so there's nothing to hang on at teardown). ---


class _FakeRulesetResponse:
    """A stand-in for an httpx.Response, faithful where the SDK depends on it.

    ``raise_for_status`` used to be a bare ``pass``, which quietly made every
    error-status test weaker than it looked: the SDK relies on that call to
    raise before it overwrites its cached ruleset, so a no-op double let a 5xx
    body through as if it were a successful fetch. A double that is more
    forgiving than the real thing tests the double.
    """

    def __init__(self, flags, status_code: int = 200):
        self._flags = flags
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._flags


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        await asyncio.Event().wait()  # keep the "connection" open until cancelled


class _FakeHTTP:
    """Emits one SSE flag-change; /ruleset returns the flag disabled on the
    bootstrap fetch and enabled on the post-event refresh."""

    def __init__(self):
        self._get_calls = 0

    def stream(self, method, url, **kwargs):
        return _FakeStream(
            [
                "event: connected", "data: ok", "",
                "event: flag-change", 'data: {"flag_key": "f", "action": "updated"}', "",
            ]
        )

    async def get(self, url):
        self._get_calls += 1
        enabled = self._get_calls >= 2  # bootstrap: off; after the SSE event: on
        return _FakeRulesetResponse(
            [{"key": "f", "enabled": enabled, "rollout_percentage": 100, "rules": []}]
        )


class _UnauthorizedHTTP:
    """Always 401 — the SDK must fail open, not raise, on a bad credential."""

    def __init__(self):
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        return _FakeRulesetResponse([], status_code=401)

    def stream(self, method, url, **kwargs):
        return _FakeStream([])


class _GoesBadHTTP:
    """Serves a good ruleset once, then breaks.

    "Fails open" is not "returns the caller's default for a key it never had" —
    that happens whether or not the SDK kept anything. The property that matters
    is that the *last known good* ruleset survives the outage, and only a client
    that has successfully loaded one can demonstrate it.
    """

    def __init__(self, failure: Exception | int):
        self.failure = failure
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        if self.calls == 1:
            return _FakeRulesetResponse(
                [{"key": "f", "enabled": True, "rollout_percentage": 100, "rules": []}]
            )
        if isinstance(self.failure, int):
            return _FakeRulesetResponse([], status_code=self.failure)
        raise self.failure

    def stream(self, method, url, **kwargs):
        return _FakeStream([])


async def test_sdk_watch_auto_refreshes_on_sse_event():
    """watch=True: a 'flag-change' SSE event triggers a local refresh."""
    sdk = RipcordClient(http_client=_FakeHTTP())
    await sdk.start(watch=True)
    try:
        assert sdk.is_enabled("f", "u1") is False  # bootstrap fetch
        for _ in range(100):
            if sdk.is_enabled("f", "u1"):
                break
            await asyncio.sleep(0.02)
        assert sdk.is_enabled("f", "u1") is True  # SSE event drove the refresh
    finally:
        await sdk.close()


async def test_sdk_fails_open_on_rejected_credential():
    """A 401 must not crash the caller's app, and must not poison the cache."""
    sdk = RipcordClient(http_client=_UnauthorizedHTTP())
    await sdk.start(watch=False)
    try:
        # Fail open: the caller's default is served, no exception escapes.
        assert sdk.is_enabled("anything", "u1") is False
        assert sdk.is_enabled("anything", "u1", default=True) is True
    finally:
        await sdk.close()


async def test_sdk_sends_its_api_key():
    """The SDK attaches the configured key to every request it makes."""
    sdk = RipcordClient(base_url="http://example.invalid", api_key="rpc_abc123_secret")
    try:
        assert sdk._http.headers["Authorization"] == "Bearer rpc_abc123_secret"
    finally:
        await sdk.close()


# --- Fail-open, tested for the property it actually claims --------------------
#
# The two tests above assert `is_enabled("anything")` after a failed bootstrap.
# That passes whether or not the SDK preserved anything, because "anything" was
# never in the ruleset — the default is returned either way. Inserting
# `self._flags = {}` into refresh()'s except branch destroys fail-open
# completely and leaves every one of those assertions passing. These two hold
# the real invariant.


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(ConnectionError("server went away"), id="transport-error"),
        pytest.param(500, id="server-error"),
        pytest.param(401, id="credential-revoked"),
    ],
)
async def test_a_failed_refresh_keeps_the_last_known_good_ruleset(failure) -> None:
    http = _GoesBadHTTP(failure)
    sdk = RipcordClient(http_client=http)
    await sdk.start(watch=False)
    try:
        assert sdk.is_enabled("f", "u1") is True  # loaded successfully

        await sdk.refresh()  # this one fails, however it fails

        assert http.calls == 2, "the failing refresh must actually have been attempted"
        assert sdk.is_enabled("f", "u1") is True, (
            "the SDK discarded its cached ruleset on a failed refresh — "
            "an outage at the flag service would turn every flag off"
        )
    finally:
        await sdk.close()


async def test_a_failed_refresh_does_not_raise_into_the_callers_app() -> None:
    """The other half: failing open must also mean failing quietly."""
    sdk = RipcordClient(http_client=_GoesBadHTTP(ConnectionError("down")))
    await sdk.start(watch=False)
    try:
        await sdk.refresh()  # must not raise
        assert sdk.is_enabled("unknown-key", "u1", default=True) is True
    finally:
        await sdk.close()
