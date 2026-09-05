"""Tests for the Redis-backed ruleset cache and the pub/sub change stream."""

import asyncio

from ripcord.api.realtime import flag_change_events
from ripcord.cache import CHANGES_CHANNEL, RULESET_KEY


async def test_ruleset_returns_flags(client):
    """/ruleset returns every flag."""
    await client.post("/flags", json={"key": "a", "name": "A"})
    resp = await client.get("/ruleset")
    assert resp.status_code == 200
    assert [f["key"] for f in resp.json()] == ["a"]


async def test_ruleset_is_cached_then_invalidated(client, redis_client):
    """A read populates the cache; a mutation invalidates it."""
    await client.post("/flags", json={"key": "a", "name": "A"})
    await client.get("/ruleset")
    assert await redis_client.get(RULESET_KEY) is not None  # cached

    await client.post("/flags", json={"key": "b", "name": "B"})
    assert await redis_client.get(RULESET_KEY) is None  # invalidated

    resp = await client.get("/ruleset")  # rebuilt from DB
    assert [f["key"] for f in resp.json()] == ["a", "b"]


async def test_mutation_publishes_change_event(client, redis_client):
    """Creating a flag publishes an event on the changes channel."""
    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe(CHANGES_CHANNEL)
        await pubsub.get_message(timeout=2)  # discard subscribe confirmation

        await client.post("/flags", json={"key": "c", "name": "C"})

        message = None
        for _ in range(20):
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1
            )
            if message is not None:
                break
        assert message is not None
        assert "c" in message["data"]


async def test_stream_generator_greets_then_forwards_changes(redis_client):
    """The SSE generator emits 'connected', then forwards a published change."""
    events = flag_change_events(redis_client)
    try:
        first = await events.__anext__()
        assert first == {"event": "connected", "data": "ok"}

        # A change published on the channel is forwarded as the next event.
        await redis_client.publish(
            CHANGES_CHANNEL, '{"flag_key": "z", "action": "created"}'
        )
        second = await asyncio.wait_for(events.__anext__(), timeout=5)
        assert second["event"] == "flag-change"
        assert "z" in second["data"]
    finally:
        await events.aclose()  # clean shutdown — no dangling subscription
