"""Tests for the Ripcord Python SDK (local evaluation + fail-open + watch)."""

import asyncio

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
    def __init__(self, flags):
        self._flags = flags

    def raise_for_status(self):
        pass

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
