"""Tests for the Redis-backed ruleset cache and the pub/sub change stream.

The cache is a Redis **hash** (one field per flag, plus a reserved completeness
marker), not a single JSON blob. That shape is what lets `/evaluate` answer with
one `HGET` and lets a single flag change invalidate a single field — so these
tests assert per-flag invalidation, and that a partially-populated hash is never
mistaken for an authoritative empty one.
"""

import asyncio

from ripcord.api.realtime import flag_change_events
from ripcord.cache import CHANGES_CHANNEL, COMPLETE_FIELD, RULESET_KEY


async def test_ruleset_returns_flags(client):
    """/ruleset returns every flag."""
    await client.post("/flags", json={"key": "a", "name": "A"})
    resp = await client.get("/ruleset")
    assert resp.status_code == 200
    assert [f["key"] for f in resp.json()] == ["a"]


async def test_ruleset_read_populates_a_complete_cache(client, redis_client):
    """A read builds the hash and marks it authoritative."""
    await client.post("/flags", json={"key": "a", "name": "A"})
    await client.get("/ruleset")

    assert await redis_client.type(RULESET_KEY) == "hash"
    assert await redis_client.hexists(RULESET_KEY, COMPLETE_FIELD)
    assert await redis_client.hexists(RULESET_KEY, "a")
    # The cache must expire on its own even if an invalidation were ever missed.
    assert await redis_client.ttl(RULESET_KEY) > 0


async def test_create_updates_one_field_and_keeps_the_cache_warm(
    client, redis_client
):
    """The point of per-flag invalidation: one change doesn't cold-start the rest."""
    await client.post("/flags", json={"key": "a", "name": "A"})
    await client.get("/ruleset")  # populate

    await client.post("/flags", json={"key": "b", "name": "B"})

    # 'a' is untouched and still cached; 'b' was added in place.
    assert await redis_client.hexists(RULESET_KEY, "a")
    assert await redis_client.hexists(RULESET_KEY, "b")
    assert await redis_client.hexists(RULESET_KEY, COMPLETE_FIELD)

    resp = await client.get("/ruleset")
    assert sorted(f["key"] for f in resp.json()) == ["a", "b"]


async def test_update_rewrites_the_cached_field(client, redis_client):
    await client.post("/flags", json={"key": "u", "name": "U"})
    await client.get("/ruleset")
    assert '"enabled":false' in (await redis_client.hget(RULESET_KEY, "u")).replace(
        ": ", ":"
    )

    await client.patch("/flags/u", json={"enabled": True, "version": 1})

    cached = (await redis_client.hget(RULESET_KEY, "u")).replace(": ", ":")
    assert '"enabled":true' in cached


async def test_delete_removes_the_cached_field(client, redis_client):
    await client.post("/flags", json={"key": "d", "name": "D"})
    await client.get("/ruleset")
    assert await redis_client.hexists(RULESET_KEY, "d")

    await client.delete("/flags/d")

    assert not await redis_client.hexists(RULESET_KEY, "d")
    assert [f["key"] for f in (await client.get("/ruleset")).json()] == []


async def test_partial_cache_is_not_treated_as_authoritative(client, redis_client):
    """A hash with no completeness marker must trigger a rebuild, not an empty read.

    This is the failure mode the reserved marker field exists to prevent:
    without it, a hash holding one flag would look like "the ruleset has exactly
    one flag" and the others would silently vanish from /ruleset.
    """
    await client.post("/flags", json={"key": "p1", "name": "P1"})
    await client.post("/flags", json={"key": "p2", "name": "P2"})
    # Simulate a cold cache that a single flag-change wrote one field into.
    await redis_client.delete(RULESET_KEY)
    await redis_client.hset(RULESET_KEY, "p1", '{"key": "p1"}')

    resp = await client.get("/ruleset")
    assert sorted(f["key"] for f in resp.json()) == ["p1", "p2"]
    assert await redis_client.hexists(RULESET_KEY, COMPLETE_FIELD)


async def test_evaluate_is_served_from_cache_without_touching_the_db(
    client, redis_client
):
    """The hot path must not hit Postgres once the cache is warm."""
    await client.post(
        "/flags",
        json={"key": "hot", "name": "Hot", "enabled": True, "rollout_percentage": 100},
    )
    await client.get("/ruleset")  # warm

    # Corrupt the cached entry to prove the answer really came from Redis.
    await redis_client.hset(
        RULESET_KEY,
        "hot",
        '{"key": "hot", "enabled": false, "rollout_percentage": 0, '
        '"rules": [], "variants": []}',
    )
    resp = await client.post(
        "/evaluate", json={"flag_key": "hot", "user_id": "u1", "context": {}}
    )
    assert resp.json()["enabled"] is False, "/evaluate did not read from the cache"


async def test_evaluate_falls_back_to_the_database_on_a_cold_cache(
    client, redis_client
):
    await client.post(
        "/flags",
        json={"key": "cold", "name": "Cold", "enabled": True, "rollout_percentage": 100},
    )
    await redis_client.delete(RULESET_KEY)

    resp = await client.post(
        "/evaluate", json={"flag_key": "cold", "user_id": "u1", "context": {}}
    )
    assert resp.json()["enabled"] is True
    # ...and the miss repopulated the cache for the next caller.
    assert await redis_client.hexists(RULESET_KEY, COMPLETE_FIELD)


async def test_evaluate_unknown_flag_is_fail_safe_from_cache(client, redis_client):
    await client.post("/flags", json={"key": "known", "name": "Known"})
    await client.get("/ruleset")

    resp = await client.post(
        "/evaluate", json={"flag_key": "ghost", "user_id": "u1", "context": {}}
    )
    assert resp.json()["enabled"] is False
    assert resp.json()["reason"] == "flag_not_found"


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

        await redis_client.publish(
            CHANGES_CHANNEL, '{"flag_key": "z", "action": "created"}'
        )
        second = await asyncio.wait_for(events.__anext__(), timeout=5)
        assert second["event"] == "flag-change"
        assert "z" in second["data"]
    finally:
        await events.aclose()  # clean shutdown — no dangling subscription


# --- The rebuild-vs-invalidation race -----------------------------------------
#
# Everything above tests invalidation and rebuild separately. The bug lived in
# the overlap: a rebuild reads the database, an operator kills a flag while that
# read is in flight, and the rebuild's DEL+HSET then republishes the pre-kill
# snapshot with a fresh 300-second TTL. Nothing is left to invalidate, so the
# killed flag keeps serving — the one thing a kill switch must never do.


async def test_a_rebuild_cannot_republish_a_snapshot_a_change_has_overtaken(
    client, redis_client
):
    """The fence, exercised directly at the cache layer.

    Driving this through HTTP would mean winning a real race, which makes for a
    flaky test. The mechanism is what matters, so the interleaving is written
    out explicitly: read the epoch, let a change land, then try to publish.
    """
    from ripcord import cache

    stale = {"doomed": '{"key": "doomed", "enabled": true}'}

    # 1. A reader starts a rebuild: epoch first, then its (slow) database read.
    epoch = await cache.read_epoch(redis_client)

    # 2. Mid-read, an operator kills the flag. This bumps the epoch.
    await cache.notify_flag_change(
        redis_client, "doomed", "updated", '{"key": "doomed", "enabled": false}'
    )

    # 3. The reader finishes and tries to publish what it read.
    published = await cache.write_ruleset(
        redis_client, stale, expected_epoch=epoch
    )

    assert published is False, "a superseded rebuild must not publish"
    assert await redis_client.hget(RULESET_KEY, "doomed") == (
        '{"key": "doomed", "enabled": false}'
    ), "the fresh field survived the rebuild"


async def test_an_uncontended_rebuild_still_publishes(client, redis_client):
    """The fence must not make the cache permanently unfillable."""
    from ripcord import cache

    epoch = await cache.read_epoch(redis_client)
    published = await cache.write_ruleset(
        redis_client, {"a": '{"key": "a"}'}, expected_epoch=epoch
    )

    assert published is True
    assert await redis_client.hexists(RULESET_KEY, COMPLETE_FIELD)


async def test_killing_a_flag_during_a_cold_rebuild_takes_effect_immediately(
    client, redis_client
):
    """The behaviour the fence exists for, asserted end to end.

    The flag is killed while the cache is cold. Whatever the cache does next,
    the very next evaluation has to say the flag is off — not in 300 seconds.
    """
    await client.post(
        "/flags",
        json={
            "key": "risky",
            "name": "Risky",
            # Enabled on purpose. A flag that was never on cannot demonstrate a
            # kill switch failing, and the first version of this test created
            # one with `enabled` left at its default of false — so it passed
            # with the fence removed, which is how I found out.
            "enabled": True,
            "rollout_percentage": 100,
        },
    )
    await redis_client.flushdb()  # cold cache, as after a Redis restart

    from ripcord import cache

    # A rebuild that began before the kill: it holds the pre-kill snapshot.
    epoch = await cache.read_epoch(redis_client)
    stale = await _mapping_from_api(client)

    killed = await client.patch(
        "/flags/risky", json={"enabled": False, "version": 1}
    )
    assert killed.status_code == 200

    # The in-flight rebuild lands late.
    await cache.write_ruleset(redis_client, stale, expected_epoch=epoch)

    evaluated = await client.post(
        "/evaluate", json={"flag_key": "risky", "user_id": "u1"}
    )
    assert evaluated.json()["enabled"] is False, (
        "a killed flag served ON — the rebuild republished a stale snapshot"
    )


async def _mapping_from_api(client) -> dict[str, str]:
    """The ruleset as a rebuild would have read it, in hash shape."""
    import json

    flags = (await client.get("/ruleset")).json()
    return {f["key"]: json.dumps(f) for f in flags}
