// k6 load test for the Ripcord /evaluate hot path.
//
//   1. Start the stack:   docker compose up -d  &&  uvicorn ripcord.main:app
//   2. Seed the flag:     curl -X POST localhost:8000/flags -H 'content-type: application/json' \
//                              -d '{"key":"load-test","name":"Load Test","enabled":true,"rollout_percentage":50}'
//   3. Run:               k6 run loadtest/evaluate.js
//
// Override the target with:  BASE_URL=http://host:port k6 run loadtest/evaluate.js
import http from "k6/http";
import { check } from "k6";

export const options = {
  vus: 50,
  duration: "20s",
  summaryTrendStats: ["avg", "min", "med", "p(95)", "p(99)", "max"],
  thresholds: {
    http_req_failed: ["rate<0.01"], // fewer than 1% errors
    // SLOs for the DB-backed /evaluate path (measured ~p95 78ms, ~p99 108ms
    // at ~1.1k RPS on a laptop). The SDK's local path is microseconds.
    http_req_duration: ["p(95)<120", "p(99)<180"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";

export default function () {
  const payload = JSON.stringify({
    flag_key: "load-test",
    user_id: `user-${__VU}-${__ITER}`,
    context: { country: "IN" },
  });
  const res = http.post(`${BASE_URL}/evaluate`, payload, {
    headers: { "Content-Type": "application/json" },
  });
  check(res, { "status is 200": (r) => r.status === 200 });
}
