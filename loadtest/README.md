# Load test

A [k6](https://k6.io/) script that hammers the `/evaluate` hot path so we can
report real throughput and tail-latency numbers (not guesses).

## Run it

```bash
# 1. stack up
docker compose up -d
uvicorn ripcord.main:app --port 8000

# 2. seed the flag the script evaluates
curl -X POST localhost:8000/flags -H 'content-type: application/json' \
  -d '{"key":"load-test","name":"Load Test","enabled":true,"rollout_percentage":50}'

# 3. load test (50 virtual users, 20s)
k6 run loadtest/evaluate.js
```

## What to read

k6 prints `http_reqs` (throughput) and the `http_req_duration` percentiles
(`p(95)`, `p(99)`). Those are the numbers quoted in the top-level README.

> Note: `/evaluate` is the *server-side* path and does a DB lookup per call. In
> production, apps use the **SDK**, which evaluates locally in microseconds with
> no network hop — this test measures the heavier server path on purpose.
