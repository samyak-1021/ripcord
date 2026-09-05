"""Tests for the Prometheus /metrics endpoint."""


async def test_metrics_endpoint_exposes_our_metrics(client):
    """/metrics returns Prometheus text including our custom counters."""
    await client.post(
        "/flags",
        json={"key": "m", "name": "M", "enabled": True, "rollout_percentage": 100},
    )
    await client.post("/evaluate", json={"flag_key": "m", "user_id": "u1"})

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "ripcord_flag_evaluations_total" in body
    assert "ripcord_http_requests_total" in body
