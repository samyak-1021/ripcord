"""End-to-end tests for the flag management API (HTTP -> service -> Postgres)."""

from sqlalchemy import select

from ripcord.models import AuditLog


async def test_create_and_get_flag(client):
    """A created flag reads back with its fields and version 1."""
    resp = await client.post(
        "/flags",
        json={"key": "new-checkout", "name": "New Checkout", "enabled": True,
              "rollout_percentage": 25},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["key"] == "new-checkout"
    assert body["enabled"] is True
    assert body["version"] == 1

    resp = await client.get("/flags/new-checkout")
    assert resp.status_code == 200
    assert resp.json()["rollout_percentage"] == 25


async def test_duplicate_key_returns_409(client):
    """Reusing a key is a conflict."""
    await client.post("/flags", json={"key": "dup", "name": "First"})
    resp = await client.post("/flags", json={"key": "dup", "name": "Second"})
    assert resp.status_code == 409


async def test_list_flags_sorted_by_key(client):
    """Listing returns every flag, ordered by key."""
    await client.post("/flags", json={"key": "b-flag", "name": "B"})
    await client.post("/flags", json={"key": "a-flag", "name": "A"})
    resp = await client.get("/flags")
    assert resp.status_code == 200
    assert [f["key"] for f in resp.json()] == ["a-flag", "b-flag"]


async def test_update_with_correct_version(client):
    """A version-matched update succeeds and bumps the version."""
    await client.post("/flags", json={"key": "f", "name": "F"})
    resp = await client.patch("/flags/f", json={"enabled": True, "version": 1})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["version"] == 2


async def test_update_with_stale_version_returns_409(client):
    """A version mismatch is rejected (optimistic locking)."""
    await client.post("/flags", json={"key": "f", "name": "F"})
    resp = await client.patch("/flags/f", json={"enabled": True, "version": 99})
    assert resp.status_code == 409


async def test_update_missing_flag_returns_404(client):
    """Updating a non-existent flag is a 404."""
    resp = await client.patch("/flags/nope", json={"version": 1})
    assert resp.status_code == 404


async def test_delete_flag(client):
    """A deleted flag is gone afterwards."""
    await client.post("/flags", json={"key": "f", "name": "F"})
    assert (await client.delete("/flags/f")).status_code == 204
    assert (await client.get("/flags/f")).status_code == 404


async def test_targeting_rules_round_trip(client):
    """Rules submitted on create come back intact."""
    resp = await client.post(
        "/flags",
        json={
            "key": "beta",
            "name": "Beta",
            "rules": [
                {"attribute": "country", "operator": "in", "values": ["IN", "US"]}
            ],
        },
    )
    assert resp.status_code == 201
    rules = resp.json()["rules"]
    assert len(rules) == 1
    assert rules[0]["operator"] == "in"
    assert rules[0]["values"] == ["IN", "US"]


async def test_rollout_out_of_range_returns_422(client):
    """Schema validation rejects an out-of-range rollout percentage."""
    resp = await client.post(
        "/flags", json={"key": "x", "name": "X", "rollout_percentage": 150}
    )
    assert resp.status_code == 422


async def test_operations_write_audit_log(client, session):
    """Create + update leave an ordered trail in the audit log."""
    await client.post("/flags", json={"key": "f", "name": "F"})
    await client.patch("/flags/f", json={"enabled": True, "version": 1})

    result = await session.execute(select(AuditLog).order_by(AuditLog.id))
    assert [row.action for row in result.scalars().all()] == ["created", "updated"]
