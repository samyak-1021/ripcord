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

> Note: `/evaluate` is the *server-side* path — a network round trip, a Redis
> lookup for the flag, and an authentication check. This test measures it on
> purpose: it is the heavier path, and the A/B above is about what happens to it
> when the flag comes from Redis instead of Postgres.
>
> In production, applications use the **SDK**, which holds the ruleset and
> decides locally with no network hop at all — 3.3 µs median, measured by
> `python scripts/bench_eval.py`. That is roughly four orders of magnitude
> cheaper than the numbers on this page, which is the whole argument for the
> SDK existing.
>
> (An earlier version of this note said `/evaluate` "does a DB lookup per call",
> which stopped being true when the Redis cache landed and directly contradicted
> the top-level README. Keeping two documents in sync by hand does not work;
> this one now states the mechanism rather than restating a number.)
