// k6 load test for the Ripcord /evaluate hot path.
//
//   1. Start the stack:   docker compose up -d
//   2. Mint a key:        python -m ripcord.cli mint-bootstrap   (or POST /keys)
//   3. Seed the flag:     curl -X POST localhost:8000/flags \
//                              -H "Authorization: Bearer $RIPCORD_KEY" \
//                              -H 'content-type: application/json' \
//                              -d '{"key":"load-test","name":"Load Test","enabled":true,"rollout_percentage":50}'
//   4. Warm the cache:    curl localhost:8000/ruleset -H "Authorization: Bearer $RIPCORD_KEY"
//   5. Run:               RIPCORD_KEY=rpc_... k6 run loadtest/evaluate.js
//
// Override the target with:  BASE_URL=http://host:port k6 run loadtest/evaluate.js
//
// To reproduce the cache-vs-database comparison in the README, point
// ripcord/api/evaluate.py at `services.evaluate_flag` (the database path)
// instead of `services.evaluate_flag_cached` and re-run this unchanged.
import http from "k6/http";
import { check, fail } from "k6";

export const options = {
  vus: 50,
  duration: "20s",
  summaryTrendStats: ["avg", "min", "med", "p(95)", "p(99)", "max"],
  thresholds: {
    http_req_failed: ["rate<0.01"], // fewer than 1% errors
    // SLOs for the Redis-backed /evaluate path. Deliberately loose enough to
    // pass on modest CI hardware while still catching a real regression —
    // moving this endpoint back onto the database blows straight through them.
    http_req_duration: ["p(95)<150", "p(99)<250"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const KEY = __ENV.RIPCORD_KEY;

export function setup() {
  if (!KEY) {
    fail("RIPCORD_KEY is not set — /evaluate requires a key with the 'sdk' scope");
  }
}

export default function () {
  const payload = JSON.stringify({
    flag_key: "load-test",
    user_id: `user-${__VU}-${__ITER}`,
    context: { country: "IN" },
  });
  const res = http.post(`${BASE_URL}/evaluate`, payload, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${KEY}`,
    },
  });
  check(res, { "status is 200": (r) => r.status === 200 });
}
