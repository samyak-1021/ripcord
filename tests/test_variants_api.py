"""Integration tests for multivariate flags through the HTTP API.

The engine tests in test_variants.py cover the maths. These cover the edges the
API is responsible for: validation the engine deliberately does not do (because
the engine must stay total), round-tripping arbitrary JSON payloads, and the
cross-check between rules and the variants actually stored on the flag.
"""

import pytest
from httpx import AsyncClient

MV = {
    "key": "checkout",
    "name": "Checkout",
    "enabled": True,
    "rollout_percentage": 100,
    "variants": [
        {"key": "control", "value": {"color": "grey"}, "weight": 50},
        {"key": "blue", "value": {"color": "blue"}, "weight": 50},
    ],
}


async def test_create_and_read_back_a_multivariate_flag(client: AsyncClient) -> None:
    created = await client.post("/flags", json=MV)
    assert created.status_code == 201

    flag = (await client.get("/flags/checkout")).json()
    assert [v["key"] for v in flag["variants"]] == ["blue", "control"]  # key order
    assert {v["key"]: v["weight"] for v in flag["variants"]} == {
        "control": 50,
        "blue": 50,
    }
    assert flag["variants"][0]["value"] == {"color": "blue"}


@pytest.mark.parametrize(
    "value",
    [
        {"nested": {"deep": [1, 2, 3]}},
        ["a", "b"],
        "a plain string",
        42,
        3.14,
        True,
        None,
    ],
)
async def test_variant_values_round_trip_any_json(
    client: AsyncClient, value
) -> None:
    """The engine never inspects a variant's value, so anything JSON must survive."""
    await client.post(
        "/flags",
        json={
            "key": "payload",
            "name": "Payload",
            "variants": [
                {"key": "a", "value": value, "weight": 100},
                {"key": "b", "value": None, "weight": 0},
            ],
        },
    )
    flag = (await client.get("/flags/payload")).json()
    stored = next(v for v in flag["variants"] if v["key"] == "a")
    assert stored["value"] == value
    await client.delete("/flags/payload")


# --- Validation the API owns -------------------------------------------------


async def test_weights_must_sum_to_100(client: AsyncClient) -> None:
    resp = await client.post(
        "/flags",
        json={
            "key": "bad",
            "name": "Bad",
            "variants": [
                {"key": "a", "weight": 30},
                {"key": "b", "weight": 30},
            ],
        },
    )
    assert resp.status_code == 422
    assert "sum to 100" in str(resp.json())


async def test_a_single_variant_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/flags",
        json={"key": "lonely", "name": "Lonely", "variants": [{"key": "a", "weight": 100}]},
    )
    assert resp.status_code == 422
    assert "at least 2" in str(resp.json())


async def test_duplicate_variant_keys_are_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/flags",
        json={
            "key": "dupe",
            "name": "Dupe",
            "variants": [
                {"key": "a", "weight": 50},
                {"key": "a", "weight": 50},
            ],
        },
    )
    assert resp.status_code == 422
    assert "duplicate" in str(resp.json()).lower()


async def test_off_variant_must_name_a_real_variant(client: AsyncClient) -> None:
    resp = await client.post(
        "/flags",
        json={**MV, "key": "badoff", "off_variant": "purple"},
    )
    assert resp.status_code == 422
    assert "off_variant" in str(resp.json())


async def test_boolean_flag_cannot_have_an_off_variant(client: AsyncClient) -> None:
    resp = await client.post(
        "/flags",
        json={"key": "booloff", "name": "Bool", "off_variant": "a"},
    )
    assert resp.status_code == 422


async def test_rule_cannot_pin_an_undefined_variant_on_create(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/flags",
        json={
            **MV,
            "key": "badrule",
            "rules": [
                {
                    "attribute": "country",
                    "operator": "in",
                    "values": ["IN"],
                    "variant": "magenta",
                }
            ],
        },
    )
    assert resp.status_code == 422
    assert "magenta" in str(resp.json())


async def test_rule_cannot_pin_an_undefined_variant_on_update(
    client: AsyncClient,
) -> None:
    """The gap the schema alone can't close.

    Editing rules without resending variants is the dashboard's normal PATCH,
    so the cross-check has to happen against the *stored* variants.
    """
    await client.post("/flags", json=MV)
    resp = await client.patch(
        "/flags/checkout",
        json={
            "version": 1,
            "rules": [
                {
                    "attribute": "country",
                    "operator": "in",
                    "values": ["IN"],
                    "variant": "magenta",
                }
            ],
        },
    )
    assert resp.status_code == 422
    assert "magenta" in resp.json()["detail"]


async def test_rule_pinning_a_real_variant_on_update_is_accepted(
    client: AsyncClient,
) -> None:
    await client.post("/flags", json=MV)
    resp = await client.patch(
        "/flags/checkout",
        json={
            "version": 1,
            "rules": [
                {
                    "attribute": "country",
                    "operator": "in",
                    "values": ["IN"],
                    "variant": "blue",
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["rules"][0]["variant"] == "blue"


# --- Evaluation through the API ----------------------------------------------


async def test_evaluate_returns_the_variant_and_its_value(
    client: AsyncClient,
) -> None:
    await client.post("/flags", json=MV)
    await client.get("/ruleset")  # warm the cache the hot path reads

    body = (
        await client.post(
            "/evaluate",
            json={"flag_key": "checkout", "user_id": "u-1", "context": {}},
        )
    ).json()
    assert body["enabled"] is True
    assert body["variant"] in {"control", "blue"}
    assert body["value"] == {"color": body["variant"] if body["variant"] == "blue" else "grey"}


async def test_evaluate_is_sticky_through_the_api(client: AsyncClient) -> None:
    await client.post("/flags", json=MV)
    seen = set()
    for _ in range(10):
        body = (
            await client.post(
                "/evaluate",
                json={"flag_key": "checkout", "user_id": "stable-user", "context": {}},
            )
        ).json()
        seen.add(body["variant"])
    assert len(seen) == 1


async def test_pinned_rule_wins_through_the_api(client: AsyncClient) -> None:
    await client.post(
        "/flags",
        json={
            **MV,
            "rules": [
                {
                    "attribute": "country",
                    "operator": "in",
                    "values": ["IN"],
                    "variant": "blue",
                }
            ],
        },
    )
    body = (
        await client.post(
            "/evaluate",
            json={
                "flag_key": "checkout",
                "user_id": "anyone",
                "context": {"country": "IN"},
            },
        )
    ).json()
    assert body["variant"] == "blue"
    assert body["reason"] == "targeting_match"


async def test_kill_switch_serves_the_off_variant(client: AsyncClient) -> None:
    await client.post("/flags", json={**MV, "off_variant": "control"})
    await client.patch("/flags/checkout", json={"version": 1, "enabled": False})

    body = (
        await client.post(
            "/evaluate",
            json={"flag_key": "checkout", "user_id": "u-1", "context": {}},
        )
    ).json()
    assert body["enabled"] is False
    assert body["reason"] == "flag_disabled"
    assert body["variant"] == "control"


async def test_boolean_flags_still_return_null_variant(client: AsyncClient) -> None:
    """Back-compat at the API boundary, not just in the engine."""
    await client.post(
        "/flags",
        json={"key": "plain", "name": "Plain", "enabled": True, "rollout_percentage": 100},
    )
    body = (
        await client.post(
            "/evaluate",
            json={"flag_key": "plain", "user_id": "u-1", "context": {}},
        )
    ).json()
    assert body["enabled"] is True
    assert body["variant"] is None
    assert body["value"] is None


async def test_sdk_serves_variants_locally(client: AsyncClient) -> None:
    """The SDK's local evaluation must agree with the server's."""
    from ripcord.sdk import RipcordClient

    await client.post("/flags", json=MV)
    sdk = RipcordClient(http_client=client)
    await sdk.start(watch=False)
    try:
        for user in ("u-1", "u-2", "u-3", "u-4"):
            server = (
                await client.post(
                    "/evaluate",
                    json={"flag_key": "checkout", "user_id": user, "context": {}},
                )
            ).json()
            assert sdk.variant("checkout", user) == server["variant"]
            assert sdk.value("checkout", user) == server["value"]
    finally:
        await sdk.close()
