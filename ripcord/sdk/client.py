"""A lightweight client that evaluates Ripcord flags locally.

How it works:
  * ``start()`` fetches the whole ruleset once and keeps it in memory.
  * ``is_enabled()`` evaluates against that in-memory copy using the *same*
    engine the server uses — so client and server can never disagree — with no
    network call on the hot path (microseconds, not milliseconds).
  * With ``watch=True`` it holds an SSE connection open and refreshes the
    ruleset whenever the server signals a change.
  * It **fails open**: if the server is unreachable, the last-known-good ruleset
    (or the caller's ``default``) is served and the app keeps running.

For this project the SDK lives in the same package as the server so it can reuse
``ripcord.engine`` directly. In production you'd extract that pure module into a
small shared library so the SDK stays dependency-light.
"""

import asyncio

import httpx

from ripcord.engine import FlagSpec, RuleSpec, VariantSpec, evaluate
from ripcord.logging_config import log


def _spec_from_json(data: dict) -> FlagSpec:
    """Build an engine FlagSpec from one /ruleset JSON entry."""
    return FlagSpec(
        key=data["key"],
        enabled=data["enabled"],
        rollout_percentage=data["rollout_percentage"],
        off_variant=data.get("off_variant"),
        rules=[
            RuleSpec(
                attribute=r["attribute"],
                operator=r["operator"],
                values=r["values"],
                variant=r.get("variant"),
            )
            for r in data["rules"]
        ],
        variants=[
            VariantSpec(key=v["key"], value=v.get("value"), weight=v["weight"])
            for v in data.get("variants", [])
        ],
    )


class RipcordClient:
    """Evaluate feature flags locally from a cached, auto-refreshed ruleset."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        reconnect_delay: float = 1.0,
    ) -> None:
        # The SDK only ever needs the `sdk` scope: it reads /ruleset and holds
        # /stream open. Shipping a `flags:write` key inside an application
        # binary would defeat the purpose of authenticating at all.
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if http_client is not None:
            self._http = http_client
            if api_key:
                self._http.headers.update(headers)
            self._owns_http = False
        elif base_url is not None:
            self._http = httpx.AsyncClient(base_url=base_url, headers=headers)
            self._owns_http = True
        else:
            raise ValueError("provide either base_url or http_client")
        self._reconnect_delay = reconnect_delay
        self._flags: dict[str, FlagSpec] = {}
        self._watch_task: asyncio.Task | None = None
        self._stopped = False

    async def start(self, *, watch: bool = True) -> None:
        """Load the ruleset once, then (optionally) watch for changes."""
        await self.refresh()
        if watch:
            self._watch_task = asyncio.create_task(self._watch())

    async def refresh(self) -> None:
        """Re-fetch the ruleset. On failure keep the last-known-good copy."""
        try:
            response = await self._http.get("/ruleset")
            if response.status_code in (401, 403):
                # A bad credential is an operator error, not a transient blip —
                # it will not fix itself on the next poll. Say so loudly, then
                # still fail open so the caller's app keeps serving traffic.
                log.error(
                    "ripcord.auth_failed",
                    status=response.status_code,
                    detail="check the SDK api_key and that it has the 'sdk' scope",
                )
                return
            response.raise_for_status()
            self._flags = {
                item["key"]: _spec_from_json(item) for item in response.json()
            }
        except Exception:
            # Fail open: flag-service problems must never crash the caller's app.
            log.warning("ripcord.refresh_failed", serving="last-known-good")

    def is_enabled(
        self,
        flag_key: str,
        user_id: str,
        context: dict[str, str] | None = None,
        *,
        default: bool = False,
    ) -> bool:
        """Evaluate a flag locally. Unknown/unloaded flags return ``default``."""
        spec = self._flags.get(flag_key)
        if spec is None:
            return default
        return evaluate(spec, user_id, context).enabled

    def variant(
        self,
        flag_key: str,
        user_id: str,
        context: dict[str, str] | None = None,
        *,
        default: str | None = None,
    ) -> str | None:
        """Return the variant key served to this user, or ``default``."""
        spec = self._flags.get(flag_key)
        if spec is None:
            return default
        result = evaluate(spec, user_id, context)
        return result.variant if result.variant is not None else default

    def value(
        self,
        flag_key: str,
        user_id: str,
        context: dict[str, str] | None = None,
        *,
        default=None,
    ):
        """Return the served variant's payload, or ``default``.

        This is the multivariate analogue of ``is_enabled``: instead of a
        boolean you get whatever config the winning variant carries.
        """
        spec = self._flags.get(flag_key)
        if spec is None:
            return default
        result = evaluate(spec, user_id, context)
        return result.value if result.variant is not None else default

    async def _watch(self) -> None:
        """Hold an SSE connection open and refresh on every change event."""
        while not self._stopped:
            try:
                async with self._http.stream(
                    "GET", "/stream", timeout=None
                ) as response:
                    event: str | None = None
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event = line.split(":", 1)[1].strip()
                        elif line.startswith("data:") and event == "flag-change":
                            await self.refresh()
                        elif line == "":
                            event = None  # blank line marks the SSE event boundary
            except Exception:
                pass  # any stream error -> fall through to the backoff below
            # Back off before reconnecting. This runs after BOTH an error and a
            # clean server-side stream close, so a proxy that drops idle SSE
            # connections can never turn this into a hot reconnect loop.
            if not self._stopped:
                await asyncio.sleep(self._reconnect_delay)

    async def close(self) -> None:
        """Stop watching and release owned resources."""
        self._stopped = True
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        if self._owns_http:
            await self._http.aclose()
