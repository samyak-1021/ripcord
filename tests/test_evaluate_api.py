"""Integration tests for the /evaluate endpoint (HTTP -> engine -> Postgres)."""


async def test_unknown_flag_defaults_off(client):
    """Evaluating a flag that doesn't exist is safe: off, never an error."""
    resp = await client.post("/evaluate", json={"flag_key": "missing", "user_id": "u1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["reason"] == "flag_not_found"


async def test_disabled_flag_evaluates_off(client):
    await client.post(
        "/flags",
        json={"key": "f", "name": "F", "enabled": False, "rollout_percentage": 100},
    )
    resp = await client.post("/evaluate", json={"flag_key": "f", "user_id": "u1"})
    assert resp.json()["enabled"] is False
    assert resp.json()["reason"] == "flag_disabled"


async def test_targeting_rule_evaluates_on(client):
    await client.post(
        "/flags",
        json={
            "key": "f", "name": "F", "enabled": True, "rollout_percentage": 0,
            "rules": [{"attribute": "country", "operator": "in", "values": ["IN"]}],
        },
    )
    resp = await client.post(
        "/evaluate",
        json={"flag_key": "f", "user_id": "u1", "context": {"country": "IN"}},
    )
    assert resp.json()["enabled"] is True
    assert resp.json()["reason"] == "targeting_match"


async def test_full_rollout_evaluates_on(client):
    await client.post(
        "/flags",
        json={"key": "f", "name": "F", "enabled": True, "rollout_percentage": 100},
    )
    resp = await client.post("/evaluate", json={"flag_key": "f", "user_id": "u1"})
    assert resp.json()["enabled"] is True
    assert resp.json()["reason"] == "rollout_included"
