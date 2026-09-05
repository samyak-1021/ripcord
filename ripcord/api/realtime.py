"""Real-time flag distribution: a cached ruleset endpoint and an SSE stream."""

import json
from collections.abc import AsyncGenerator

import redis.asyncio as redis
from fastapi import APIRouter, Response
from sse_starlette.sse import EventSourceResponse

from ripcord import cache, services
from ripcord.deps import RedisDep, SessionDep
from ripcord.schemas import FlagOut

router = APIRouter(tags=["realtime"])


@router.get("/ruleset", response_model=list[FlagOut])
async def get_ruleset(session: SessionDep, redis_client: RedisDep) -> Response:
    """Return every flag (for SDK bootstrap), served from a Redis cache.

    Cache-aside: serve the cached JSON if present; otherwise load from Postgres,
    cache it (with a TTL safety net), and return it.
    """
    cached = await cache.read_ruleset(redis_client)
    if cached is not None:
        return Response(content=cached, media_type="application/json")

    flags = await services.list_flags(session)
    payload = json.dumps(
        [FlagOut.model_validate(f).model_dump(mode="json") for f in flags]
    )
    await cache.write_ruleset(redis_client, payload)
    return Response(content=payload, media_type="application/json")


async def flag_change_events(client: redis.Redis) -> AsyncGenerator[dict, None]:
    """Yield a 'connected' event, then one 'flag-change' event per change.

    Extracted from the route so it can be unit-tested with a controllable
    lifecycle, rather than through an infinite HTTP stream that is awkward to
    tear down inside a test.
    """
    async with client.pubsub() as pubsub:
        await pubsub.subscribe(cache.CHANGES_CHANNEL)
        # Greet the client immediately (also flushes response headers).
        yield {"event": "connected", "data": "ok"}
        while True:
            # A timeout keeps this await cancellable and lets sse-starlette
            # send its keep-alive pings between checks.
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=15.0
            )
            if message is not None:
                yield {"event": "flag-change", "data": message["data"]}


@router.get("/stream")
async def stream(redis_client: RedisDep) -> EventSourceResponse:
    """Server-Sent Events: emit a 'flag-change' event whenever a flag changes.

    Every server subscribes to the same Redis channel, so a change made on any
    one server reaches all connected clients — the "update without a redeploy"
    path. Clients react by re-fetching /ruleset.
    """
    return EventSourceResponse(flag_change_events(redis_client))
