"""Redis: a cached ruleset plus a pub/sub channel for change notifications.

Two responsibilities:
  * Cache the ruleset so both the SDK-facing ``/ruleset`` read and the
    server-side ``/evaluate`` hot path avoid a database round-trip.
  * Broadcast a small event whenever a flag changes, so every server's SSE
    stream can tell connected clients to refresh — a flag flip reaches all
    servers within a second, with no redeploy and no polling.

**Why a hash rather than one JSON blob.** ``/ruleset`` wants every flag, but
``/evaluate`` wants exactly one. Storing the ruleset as a Redis hash keyed by
flag key means ``/evaluate`` is a single ``HGET`` that parses one small object,
instead of fetching and parsing the entire ruleset to answer one question. It
also makes invalidation per-flag: changing one flag rewrites one field.

**The completeness marker.** An empty hash is ambiguous — it could mean "no
flags exist" or "the cache is cold". A reserved ``__ruleset__`` field
distinguishes the two. Flag keys are validated against ``^[a-z0-9][a-z0-9._-]*$``
so a real key can never collide with it.
"""

import json

import redis.asyncio as redis

from ripcord.config import settings
from ripcord.logging_config import log

# The hash holding one field per flag, plus the completeness marker.
RULESET_KEY = "ripcord:ruleset"

# Reserved field marking the hash as fully populated. Unreachable as a flag key.
COMPLETE_FIELD = "__ruleset__"

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


async def read_ruleset(client: redis.Redis) -> list[str] | None:
    """Return every cached flag's JSON, or None if the cache is cold.

    None means "rebuild me" — it is never confused with "there are no flags",
    which comes back as an empty list.
    """
    entries = await client.hgetall(RULESET_KEY)
    if not entries or COMPLETE_FIELD not in entries:
        return None
    return [v for k, v in entries.items() if k != COMPLETE_FIELD]


async def read_flag(client: redis.Redis, flag_key: str) -> str | None | bool:
    """Return one flag's cached JSON.

    Three outcomes, which the caller must tell apart:
      * ``str``   — a cache hit.
      * ``False`` — the cache is authoritative and this flag does not exist.
      * ``None``  — the cache is cold; fall back to the database.
    """
    async with client.pipeline(transaction=False) as pipe:
        pipe.hget(RULESET_KEY, flag_key)
        pipe.hexists(RULESET_KEY, COMPLETE_FIELD)
        payload, complete = await pipe.execute()
    if payload is not None:
        return payload
    return False if complete else None


async def write_ruleset(client: redis.Redis, mapping: dict[str, str]) -> None:
    """Replace the cached ruleset with ``mapping`` and mark it complete.

    Written in one transaction so a concurrent reader never observes a
    half-populated hash marked complete.
    """
    async with client.pipeline(transaction=True) as pipe:
        pipe.delete(RULESET_KEY)
        pipe.hset(RULESET_KEY, mapping={**mapping, COMPLETE_FIELD: "1"})
        pipe.expire(RULESET_KEY, RULESET_TTL_SECONDS)
        await pipe.execute()


async def notify_flag_change(
    client: redis.Redis, flag_key: str, action: str, payload: str | None = None
) -> None:
    """Update the cached flag in place and publish a change event.

    Per-flag invalidation: a change to one flag rewrites (or removes) one hash
    field and leaves the rest of the cache warm, rather than forcing every
    server to rebuild the whole ruleset on the next request.

    Best-effort: the database write has already committed by the time we get
    here, so a Redis hiccup must never turn a successful change into a 500.
    Worst case the cache self-heals via its TTL and clients refresh on their
    next reconnect.
    """
    try:
        if action == "deleted":
            await client.hdel(RULESET_KEY, flag_key)
        elif payload is not None:
            # Write the field unconditionally. If the hash was cold this
            # creates a *partial* one with no completeness marker, which reads
            # handle safely: a present field is fresh and served, an absent one
            # falls back to the database. Gating this on "does the hash exist?"
            # would instead open a race — a concurrent cold-cache rebuild could
            # miss this flag and hide it until the TTL expired.
            await client.hset(RULESET_KEY, flag_key, payload)
            if await client.ttl(RULESET_KEY) < 0:
                await client.expire(RULESET_KEY, RULESET_TTL_SECONDS)
        else:
            await client.delete(RULESET_KEY)
        await client.publish(
            CHANGES_CHANNEL, json.dumps({"flag_key": flag_key, "action": action})
        )
    except Exception:
        log.warning("cache.notify_failed", flag_key=flag_key, action=action)
