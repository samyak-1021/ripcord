"""Redis: a cached ruleset plus a pub/sub channel for change notifications.

Two responsibilities:
  * Cache the full ruleset (all flags) so the SDK-facing `/ruleset` read is a
    single fast Redis GET instead of a database query on every poll.
  * Broadcast a small event whenever a flag changes, so every server's SSE
    stream can tell connected clients to refresh — a flag flip reaches all
    servers within a second, with no redeploy and no polling.
"""

import json

import redis.asyncio as redis

from ripcord.config import settings

# A single cache key holding the whole ruleset. Whole-ruleset granularity keeps
# invalidation trivially correct; per-flag keys would scale better but aren't
# needed at this size.
RULESET_KEY = "ripcord:ruleset"

# Channel that mutations publish to and every SSE stream subscribes to.
CHANGES_CHANNEL = "ripcord:flag-changes"

# TTL safety net: even if an invalidation were ever missed, the cache
# self-heals within this window.
RULESET_TTL_SECONDS = 300

# One connection pool per process (lazy — no connection until first use).
_client = redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> redis.Redis:
    """FastAPI dependency returning the shared Redis client."""
    return _client


async def read_ruleset(client: redis.Redis) -> str | None:
    """Return the cached ruleset JSON, or None on a cache miss."""
    return await client.get(RULESET_KEY)


async def write_ruleset(client: redis.Redis, payload: str) -> None:
    """Store the ruleset JSON with a TTL safety net."""
    await client.set(RULESET_KEY, payload, ex=RULESET_TTL_SECONDS)


async def notify_flag_change(client: redis.Redis, flag_key: str, action: str) -> None:
    """Invalidate the cached ruleset and publish a change event to subscribers."""
    await client.delete(RULESET_KEY)
    await client.publish(
        CHANGES_CHANNEL, json.dumps({"flag_key": flag_key, "action": action})
    )
