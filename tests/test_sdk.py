"""Tests for the Ripcord Python SDK (local evaluation + fail-open)."""

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
