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

    # The breakdown has to be consistent with the *reasons* the engine returned.
    #
    # The previous assertion compared `sum(by_result.values())` to
    # `evaluations_total` — but the handler defines the total as that sum, so it
    # compared a value to itself and could never fail. An identity is not a test.
    assert body["evaluations_by_result"], "the breakdown must not be empty"
    assert set(body["evaluations_by_result"]) <= KNOWN_REASONS, (
        f"unexpected evaluation reason: {body['evaluations_by_result']}"
    )


# Every reason the engine can attribute an evaluation to. Listed explicitly so
# a new reason that never reaches the metrics layer shows up as a failure here
# rather than as a silently missing bar on the dashboard.
KNOWN_REASONS = {
    "rollout_included",
    "rollout_excluded",
    "flag_disabled",
    "flag_not_found",
    "targeting_match",
}


async def test_stats_attributes_each_evaluation_to_its_reason(client):
    """Two evaluations with opposite outcomes land in different buckets.

    Asserted as a *delta*. The Prometheus counter lives in the process, not in
    the database the fixtures reset, so every earlier test in the session has
    already incremented it — an absolute assertion here would pass or fail
    depending on which other tests ran first.
    """
    before = (await client.get("/stats")).json()

    await client.post(
        "/flags",
        json={"key": "on", "name": "On", "enabled": True, "rollout_percentage": 100},
    )
    await client.post("/flags", json={"key": "off", "name": "Off", "enabled": False})
    await client.post("/evaluate", json={"flag_key": "on", "user_id": "u1"})
    await client.post("/evaluate", json={"flag_key": "off", "user_id": "u1"})

    after = (await client.get("/stats")).json()

    def delta(field: str) -> dict[str, int]:
        prior = before[field]
        return {
            reason: count - prior.get(reason, 0)
            for reason, count in after[field].items()
            if count - prior.get(reason, 0)
        }

    assert delta("evaluations_by_result") == {
        "rollout_included": 1,
        "flag_disabled": 1,
    }
    assert after["evaluations_total"] - before["evaluations_total"] == 2
