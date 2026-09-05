"""Tests for the audit-log and stats read endpoints."""


async def test_audit_lists_recent_changes(client):
    await client.post("/flags", json={"key": "a", "name": "A"})
    await client.patch("/flags/a", json={"enabled": True, "version": 1})

    resp = await client.get("/audit")
    assert resp.status_code == 200
    actions = [entry["action"] for entry in resp.json()]
    assert "created" in actions
    assert "updated" in actions


async def test_audit_can_filter_by_flag(client):
    await client.post("/flags", json={"key": "a", "name": "A"})
    await client.post("/flags", json={"key": "b", "name": "B"})

    resp = await client.get("/audit", params={"flag_key": "a"})
    assert resp.status_code == 200
    assert [e["flag_key"] for e in resp.json()] == ["a"]


async def test_stats_counts_flags(client):
    await client.post("/flags", json={"key": "on", "name": "On", "enabled": True})
    await client.post("/flags", json={"key": "off", "name": "Off", "enabled": False})

    await client.post("/evaluate", json={"flag_key": "on", "user_id": "u1"})

    resp = await client.get("/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["flags_total"] == 2
    assert body["flags_enabled"] == 1
    assert body["flags_disabled"] == 1
    # Must be a real count, not the counter's `_created` timestamp (~1.7e9).
    assert 0 < body["evaluations_total"] < 1_000_000
    # The per-result breakdown must be consistent with the total.
    assert sum(body["evaluations_by_result"].values()) == body["evaluations_total"]
