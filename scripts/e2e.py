#!/usr/bin/env python3
"""End-to-end smoke test against a *running* Ripcord.

The pytest suite exercises the app in-process with an ASGI transport. That is
fast and catches most things, but it never proves the thing you actually ship
works: a real uvicorn process, real Postgres, real Redis, real HTTP, real SSE
over a socket, and a real migrated schema rather than ``Base.metadata.create_all``.

This script covers that gap. It is deliberately dependency-light (httpx only,
already a runtime dependency) and it asserts loudly, so CI can run it against
``docker compose up`` and fail the build on a regression.

    python scripts/e2e.py                       # against localhost:8000
    BASE_URL=http://api:8000 python scripts/e2e.py

``BOOTSTRAP_ADMIN_KEY`` must match what the server was started with.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
BOOTSTRAP = os.environ.get("BOOTSTRAP_ADMIN_KEY")

_passed = 0
_failed: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  \033[32mPASS\033[0m  {label}")
    else:
        _failed.append(label)
        print(f"  \033[31mFAIL\033[0m  {label}{f' — {detail}' if detail else ''}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


async def wait_for_health(client: httpx.AsyncClient, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = await client.get("/health")
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)
    raise SystemExit(f"service at {BASE_URL} never became healthy")


async def main() -> int:
    if not BOOTSTRAP:
        raise SystemExit("BOOTSTRAP_ADMIN_KEY is not set")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        await wait_for_health(client)

        boot = {"Authorization": f"Bearer {BOOTSTRAP}"}

        section("Public surface")
        health = await client.get("/health")
        check("GET /health needs no credential", health.status_code == 200)
        check("health reports ok", health.json().get("status") == "ok")
        check(
            "GET /metrics needs no credential",
            (await client.get("/metrics")).status_code == 200,
        )

        section("Unauthenticated requests are rejected")
        for method, path in [
            ("GET", "/flags"),
            ("POST", "/flags"),
            ("POST", "/evaluate"),
            ("GET", "/ruleset"),
            ("GET", "/audit"),
            ("GET", "/keys"),
        ]:
            r = await client.request(method, path, json={})
            check(f"{method} {path} -> 401", r.status_code == 401, f"got {r.status_code}")

        section("Key management")
        r = await client.post(
            "/keys", headers=boot, json={"name": "e2e-admin", "scopes": ["admin"]}
        )
        check("bootstrap key can mint an admin key", r.status_code == 201)
        admin_key = r.json()["key"]
        admin = {"Authorization": f"Bearer {admin_key}"}
        check("minted key is returned once, in full", admin_key.startswith("rpc_"))

        r = await client.post(
            "/keys", headers=admin, json={"name": "e2e-sdk", "scopes": ["sdk"]}
        )
        sdk_key = r.json()["key"]
        sdk = {"Authorization": f"Bearer {sdk_key}"}

        r = await client.post(
            "/keys", headers=admin, json={"name": "e2e-reader", "scopes": ["flags:read"]}
        )
        reader_key = r.json()["key"]
        reader = {"Authorization": f"Bearer {reader_key}"}

        listed = (await client.get("/keys", headers=admin)).json()
        check("listing keys never leaks a secret", all("key" not in k for k in listed))

        r = await client.post(
            "/keys", headers=admin, json={"name": "bad", "scopes": ["flags:wirte"]}
        )
        check("unknown scope is rejected", r.status_code == 422)

        section("Scope enforcement")
        check(
            "sdk key can read /ruleset",
            (await client.get("/ruleset", headers=sdk)).status_code == 200,
        )
        r = await client.post(
            "/flags", headers=sdk, json={"key": "evil", "name": "Evil"}
        )
        check("sdk key CANNOT create a flag", r.status_code == 403, f"got {r.status_code}")
        check(
            "reader key can list flags",
            (await client.get("/flags", headers=reader)).status_code == 200,
        )
        check(
            "reader key CANNOT manage keys",
            (await client.get("/keys", headers=reader)).status_code == 403,
        )
        check(
            "X-API-Key header style also works",
            (await client.get("/flags", headers={"X-API-Key": reader_key})).status_code
            == 200,
        )

        section("Boolean flag lifecycle")
        await client.delete("/flags/e2e-bool", headers=admin)
        r = await client.post(
            "/flags",
            headers=admin,
            json={
                "key": "e2e-bool",
                "name": "E2E Boolean",
                "enabled": True,
                "rollout_percentage": 100,
            },
        )
        check("create flag -> 201", r.status_code == 201, r.text[:120])
        version = r.json()["version"]

        r = await client.post(
            "/evaluate",
            headers=sdk,
            json={"flag_key": "e2e-bool", "user_id": "u-1", "context": {}},
        )
        body = r.json()
        check("100% rollout evaluates on", body["enabled"] is True)
        check("boolean flag returns a null variant", body["variant"] is None)

        r = await client.patch(
            "/flags/e2e-bool", headers=admin, json={"version": version, "enabled": False}
        )
        check("kill switch update -> 200", r.status_code == 200)
        # The cache must reflect the change without waiting for a TTL.
        r = await client.post(
            "/evaluate",
            headers=sdk,
            json={"flag_key": "e2e-bool", "user_id": "u-1", "context": {}},
        )
        check(
            "kill switch takes effect immediately through the cache",
            r.json()["enabled"] is False and r.json()["reason"] == "flag_disabled",
            json.dumps(r.json()),
        )

        r = await client.patch(
            "/flags/e2e-bool", headers=admin, json={"version": version, "enabled": True}
        )
        check("stale version is rejected with 409", r.status_code == 409, f"got {r.status_code}")

        section("Multivariate flag")
        await client.delete("/flags/e2e-mv", headers=admin)
        r = await client.post(
            "/flags",
            headers=admin,
            json={
                "key": "e2e-mv",
                "name": "E2E Multivariate",
                "enabled": True,
                "rollout_percentage": 100,
                "variants": [
                    {"key": "control", "value": {"c": "grey"}, "weight": 50},
                    {"key": "blue", "value": {"c": "blue"}, "weight": 50},
                ],
                "off_variant": "control",
            },
        )
        check("create multivariate flag -> 201", r.status_code == 201, r.text[:160])

        r = await client.post(
            "/flags",
            headers=admin,
            json={
                "key": "e2e-badweights",
                "name": "Bad",
                "variants": [
                    {"key": "a", "weight": 30},
                    {"key": "b", "weight": 30},
                ],
            },
        )
        check("weights not summing to 100 are rejected", r.status_code == 422)

        # Distribution + stickiness over real HTTP.
        counts: dict[str, int] = {}
        first_pass: dict[str, str] = {}
        for i in range(400):
            user = f"e2e-user-{i}"
            v = (
                await client.post(
                    "/evaluate",
                    headers=sdk,
                    json={"flag_key": "e2e-mv", "user_id": user, "context": {}},
                )
            ).json()["variant"]
            counts[v] = counts.get(v, 0) + 1
            first_pass[user] = v
        check(
            "both variants are served",
            set(counts) == {"control", "blue"},
            str(counts),
        )
        share = 100 * counts.get("blue", 0) / 400
        check(
            f"50/50 split holds over HTTP (blue={share:.1f}%)",
            35 < share < 65,
            str(counts),
        )

        resampled = {
            user: (
                await client.post(
                    "/evaluate",
                    headers=sdk,
                    json={"flag_key": "e2e-mv", "user_id": user, "context": {}},
                )
            ).json()["variant"]
            for user in list(first_pass)[:50]
        }
        check(
            "variant assignment is sticky across requests",
            all(first_pass[u] == v for u, v in resampled.items()),
        )

        section("SSE propagation")
        received: list[str] = []

        async def listen() -> None:
            async with client.stream(
                "GET", f"/stream?api_key={sdk_key}", timeout=30.0
            ) as response:
                if response.status_code != 200:
                    return
                event = None
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event == "flag-change":
                        received.append(line)
                        return

        task = asyncio.create_task(listen())
        await asyncio.sleep(1.5)  # let the subscription establish
        current = (await client.get("/flags/e2e-mv", headers=admin)).json()["version"]
        # Timed, because the README claims a number. Everything before this
        # point asserts that a change *arrives*; this asserts how fast.
        changed_at = time.perf_counter()
        await client.patch(
            "/flags/e2e-mv",
            headers=admin,
            json={"version": current, "rollout_percentage": 90},
        )
        try:
            await asyncio.wait_for(task, timeout=15)
        except TimeoutError:
            task.cancel()
        propagation_ms = (time.perf_counter() - changed_at) * 1000
        check(
            "a flag change reaches an SSE subscriber",
            len(received) == 1,
            f"received {len(received)} events",
        )
        check(
            "SSE stream authenticates via ?api_key= (EventSource can't set headers)",
            len(received) == 1,
        )
        # The README says updates land in under a second. That was an
        # unmeasured claim until this check existed; the generous bound is
        # deliberate, because the useful assertion is "sub-second, not
        # sub-minute" and a tight one would flake on a loaded CI runner.
        check(
            f"the change propagated in under 1s ({propagation_ms:.0f}ms)",
            len(received) == 1 and propagation_ms < 1000,
            f"{propagation_ms:.0f}ms",
        )

        section("Editing a multivariate flag")
        # This is the operation the dashboard's "Save variants" button performs,
        # and it used to be a hard 500: replacing the variant collection made
        # SQLAlchemy insert the new rows before deleting the old ones, so the
        # (flag_id, key) unique constraint fired for any key kept across the
        # edit. Nothing in the suite edited variants, so nothing caught it.
        mv = (await client.get("/flags/e2e-mv", headers=admin)).json()
        keys = [v["key"] for v in mv["variants"]]
        reweighted = await client.patch(
            "/flags/e2e-mv",
            headers=admin,
            json={
                "version": mv["version"],
                "variants": [
                    {"key": keys[0], "value": {"n": 1}, "weight": 70},
                    {"key": keys[1], "value": {"n": 2}, "weight": 30},
                ],
            },
        )
        check(
            "reweighting existing variants succeeds",
            reweighted.status_code == 200,
            f"got {reweighted.status_code}: {reweighted.text[:160]}",
        )
        check(
            "the new weights were stored",
            reweighted.status_code == 200
            and {v["key"]: v["weight"] for v in reweighted.json()["variants"]}
            == {keys[0]: 70, keys[1]: 30},
        )
        off = await client.patch(
            "/flags/e2e-mv",
            headers=admin,
            json={
                "version": reweighted.json()["version"],
                "off_variant": keys[0],
            },
        )
        check(
            "off_variant can be set on its own",
            off.status_code == 200 and off.json()["off_variant"] == keys[0],
            f"got {off.status_code}: {off.text[:160]}",
        )
        bad_limit = await client.get("/audit?limit=-1", headers=admin)
        check(
            "a negative audit limit is a 422, not a 500",
            bad_limit.status_code == 422,
            f"got {bad_limit.status_code}",
        )

        section("Audit attribution")
        entries = (await client.get("/audit?flag_key=e2e-mv", headers=admin)).json()
        check("audit log has entries for the flag", len(entries) > 0)
        check(
            "changes are attributed to the API key, not 'system'",
            entries and entries[0]["actor"] == "e2e-admin",
            entries[0]["actor"] if entries else "no entries",
        )

        section("Revocation")
        r = await client.post(
            "/keys", headers=admin, json={"name": "e2e-doomed", "scopes": ["flags:read"]}
        )
        doomed = r.json()["key"]
        doomed_id = r.json()["key_id"]
        check(
            "new key works before revocation",
            (await client.get("/flags", headers={"Authorization": f"Bearer {doomed}"})).status_code
            == 200,
        )
        await client.delete(f"/keys/{doomed_id}", headers=admin)
        check(
            "revoked key stops working immediately",
            (await client.get("/flags", headers={"Authorization": f"Bearer {doomed}"})).status_code
            == 401,
        )

        section("Cleanup")
        for key in ("e2e-bool", "e2e-mv"):
            r = await client.delete(f"/flags/{key}", headers=admin)
            check(f"delete {key}", r.status_code in (204, 404))

    print(f"\n{'=' * 56}")
    if _failed:
        print(f"\033[31m{len(_failed)} FAILED\033[0m, {_passed} passed")
        for name in _failed:
            print(f"  - {name}")
        return 1
    print(f"\033[32mAll {_passed} end-to-end checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
