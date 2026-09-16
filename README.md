# Ripcord

[![CI](https://github.com/samyak-1021/ripcord/actions/workflows/ci.yml/badge.svg)](https://github.com/samyak-1021/ripcord/actions/workflows/ci.yml)

A self-hostable **feature-flag & gradual-rollout service** — a LaunchDarkly-lite you
can run yourself. Toggle features on/off, target users by attributes, roll them out to
a percentage of traffic, and **pull the ripcord to kill a bad feature instantly** — no
redeploy.

> Shipping to 100% of users on day one is risky. Real teams release to 5% first, target
> by country/plan, watch the metrics, and kill anything that misbehaves — all without a
> code deploy. Ripcord is a small, honest version of exactly that.

<!-- Add a dashboard screenshot/GIF here — e.g. docs/dashboard.png -->

## Highlights

- **Sticky, monotonic percentage rollouts** — consistent-hash bucketing means a user
  never flip-flops, and raising the rollout only ever *adds* users.
- **Multivariate flags** — weighted variants (`control` / `blue` / `green`) each carrying
  arbitrary JSON, with variant choice hashed on a **different salt** from rollout
  inclusion so a partial rollout doesn't skew the split.
- **Attribute targeting** — rule DSL (`in` / `not_in` / `eq` / `neq`) that overrides the rollout.
- **Instant kill switch** — disable a flag and it's off for everyone on the next
  evaluation. Cache rebuilds are fenced against concurrent changes so an in-flight
  rebuild can't republish a snapshot the kill has already overtaken ([why that
  matters](#the-rebuild-race)).
- **Real-time propagation** — a change is broadcast over Redis pub/sub and pushed to every
  client via **Server-Sent Events**. Measured at **20 ms** end to end against a real
  server; `scripts/e2e.py` asserts it stays under a second.
- **A Python SDK that evaluates locally** — fetches the ruleset once, then decides with no
  network hop at all: **3.3 µs median, 8 µs p99** (`python scripts/bench_eval.py`).
  Auto-refreshes over SSE, and **fails open** — a failed refresh keeps the last known
  good ruleset rather than turning every flag off.
- **Scoped API keys** — SHA-256-digested keys with granular scopes, so the credential
  you ship inside your app (`sdk`) *cannot* flip a flag. Every change is attributed to
  the key that made it in the audit log.
- **Optimistic concurrency** — versioned updates reject lost writes with a `409`.
- **Redis-backed hot path** — `/evaluate` answers from a Redis hash rather than querying
  Postgres for the flag. Measured **4.1× throughput and 6.4× lower p99** than the database
  path ([numbers](#load-test)).
- **Observability + load-tested** — structured JSON logs, Prometheus `/metrics`, and a k6 suite.

## Architecture

```mermaid
flowchart LR
    subgraph clients [Clients]
      DASH[Next.js dashboard]
      APP[Your app + Ripcord SDK]
    end
    subgraph api [Ripcord API - FastAPI]
      REST[REST + SSE]
      ENG[Evaluation engine]
    end
    PG[(PostgreSQL)]
    RD[(Redis - cache + pub/sub)]

    DASH -->|REST + SSE| REST
    APP -->|/ruleset once, then /stream| REST
    REST --> ENG
    REST -->|write flags| PG
    REST -->|/evaluate: single HGET| RD
    REST -->|cache miss: rebuild| PG
    RD -. flag-change events .-> REST
```

The **evaluation engine** is a pure, dependency-free module shared by the server *and* the
SDK, so client and server can never disagree. Order of precedence:

1. **Master switch off** → off for everyone (the kill switch).
2. **A matching targeting rule** → on (rules are allow-list overrides).
3. **Percentage rollout** by sticky bucket → on / off.

## API

| Method | Path | Purpose | Required scope |
|---|---|---|---|
| `GET` | `/health` | Liveness probe | *(public)* |
| `GET` | `/metrics` | Prometheus metrics | *(public)* |
| `POST` | `/flags` | Create a flag | `flags:write` |
| `GET` | `/flags` | List all flags | `flags:read` |
| `GET` | `/flags/{key}` | Get one flag | `flags:read` |
| `PATCH` | `/flags/{key}` | Update a flag (version-checked, optimistic locking) | `flags:write` |
| `DELETE` | `/flags/{key}` | Delete a flag | `flags:write` |
| `POST` | `/evaluate` | Evaluate a flag for a user + context | `sdk` |
| `GET` | `/ruleset` | Full ruleset for SDK bootstrap (Redis-cached) | `sdk` |
| `GET` | `/stream` | SSE stream of `flag-change` events | `sdk` |
| `GET` | `/audit` | Recent change history (optional `?flag_key=`) | `flags:read` |
| `GET` | `/stats` | Flag counts + evaluation totals | `flags:read` |
| `POST` | `/keys` | Mint an API key (secret returned once) | `admin` |
| `GET` | `/keys` | List keys (never returns secrets) | `admin` |
| `DELETE` | `/keys/{key_id}` | Revoke a key | `admin` |

## Tech stack

| Area | Choice |
|---|---|
| API | FastAPI (async) + Pydantic v2 |
| Data | PostgreSQL + async SQLAlchemy 2.0 + Alembic |
| Cache / real-time | Redis (ruleset hash + pub/sub) + Server-Sent Events |
| Auth | Scoped API keys (SHA-256 digest, constant-time compare) |
| SDK | Installable Python client (local evaluation, fail-open) |
| Dashboard | Next.js 16 + React + Tailwind CSS |
| Quality | pytest + testcontainers, Ruff, k6 |
| Ops | Docker, docker-compose, GitHub Actions CI, Terraform |

## Quickstart

**Everything in Docker** (API + Postgres + Redis + dashboard):

```bash
docker compose up -d --build
curl localhost:8000/health
# -> {"status":"ok","service":"ripcord","version":"0.1.0"}
```

Auth is on by default. `docker compose` ships a clearly-labelled development
bootstrap key so this works in one command; mint your own before doing anything real
(see [Authentication](#authentication)). Open the dashboard at
<http://localhost:3000> and paste a key with at least `flags:read`.

**Or run the API on the host** (deps in Docker):

```bash
docker compose up -d postgres redis
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn ripcord.main:app --reload
```

**Dashboard:**

```bash
cd dashboard
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

## Using the SDK

The SDK loads the ruleset once and evaluates **locally** — no network call per check.

```python
from ripcord.sdk import RipcordClient

# An `sdk`-scoped key: enough to read the ruleset, not enough to change a flag.
client = RipcordClient(base_url="http://localhost:8000", api_key="rpc_...")
await client.start()   # bootstrap the ruleset + watch for changes over SSE

# Evaluated in-process, in microseconds. Falls back to `default` if the flag
# is unknown or the service is unreachable (fail-open).
if client.is_enabled("new-checkout", user_id="u-123", context={"country": "IN"}):
    show_new_checkout()

# Multivariate flags resolve locally too — same engine, same answer as the server.
match client.variant("checkout-button", user_id="u-123"):
    case "blue":
        render_blue()
    case _:
        render_control()

theme = client.value("checkout-button", user_id="u-123", default={"color": "grey"})

await client.close()
```

## Authentication

Every endpoint requires an API key except `/health`, `/metrics`, and the
OpenAPI docs pages (`/docs`, `/redoc`, `/openapi.json`). Send it as
`Authorization: Bearer <key>` (or `X-API-Key: <key>` — some proxies strip
`Authorization`).

That list is not prose: `tests/test_auth.py` reads the app's own OpenAPI schema
and asserts a 401 on every route that isn't on it, so a new endpoint is covered
the moment it exists rather than when someone remembers to add it to a list.
Two routes had already drifted off the hand-written version.

**Scopes.** A key carries an explicit set, and `admin` implies all of them:

| Scope | Grants |
|---|---|
| `flags:read` | read flags, audit log, stats |
| `flags:write` | create / update / delete flags |
| `sdk` | `/evaluate`, `/ruleset`, `/stream` |
| `admin` | manage API keys (and everything above) |

Separating `sdk` from `flags:write` is the point: the credential you ship inside
your application binary can read the ruleset but **cannot flip a flag**.

**Bootstrapping** — you need a key to mint a key, so:

```bash
python -m ripcord.cli mint-bootstrap          # prints a BOOTSTRAP_ADMIN_KEY value
export BOOTSTRAP_ADMIN_KEY=rpc_...            # then start the server

curl -X POST localhost:8000/keys \
  -H "Authorization: Bearer $BOOTSTRAP_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"admin","scopes":["admin"]}'    # unset BOOTSTRAP_ADMIN_KEY afterwards
```

Or write one straight into the database (first deploy, disaster recovery, CI):

```bash
python -m ripcord.cli create-key --name ci-deploy --scopes flags:read,flags:write
```

**How keys are stored.** A key is `rpc_<key_id>_<secret>`. Only `key_id` is
stored in plaintext (indexed, so verification is one lookup); the secret half is
kept as a SHA-256 digest and compared with `hmac.compare_digest`. There is no
"show key again" endpoint, because the secret genuinely is not recoverable.

> **Why SHA-256 rather than bcrypt/argon2?** Slow KDFs exist to protect
> low-entropy human passwords from offline brute force. These secrets are 256
> bits from `secrets.token_urlsafe`, so brute force isn't the threat — and a slow
> hash would add latency to every authenticated request. If keys were ever
> user-chosen, that reasoning would invert.

Revocation is a tombstone rather than a delete, so the audit log's reference to a
key's name survives it. Auth can be disabled with `AUTH_ENABLED=false` for local
experiments; the service logs a warning at startup when it is off.


## Multivariate flags

A flag with no variants is a plain boolean flag. Add two or more and it becomes a
weighted experiment — same table, same evaluation engine, no second code path.

```bash
curl -X POST localhost:8000/flags -H "Authorization: Bearer $RIPCORD_KEY" \
  -H "Content-Type: application/json" -d '{
    "key": "checkout-button",
    "name": "Checkout button",
    "enabled": true,
    "rollout_percentage": 20,
    "variants": [
      {"key": "control", "value": {"color": "grey"}, "weight": 50},
      {"key": "blue",    "value": {"color": "blue"}, "weight": 50}
    ],
    "off_variant": "control"
  }'
```

That reads as: *20% of users are in the experiment; those users split 50/50 between
`control` and `blue`; everyone else gets `control`.*

**Precedence** is unchanged from the boolean case, with one addition — a targeting rule
may pin a variant (`"users in IN get blue"`):

1. Master switch off → the `off_variant`.
2. A matching targeting rule → its pinned variant, else the weighted split.
3. Inside the rollout → the weighted split. Outside → the `off_variant`.

Three properties the engine holds, each with a test named after it:

- **Sticky** — a given (flag, user) always resolves the same way.
- **Order-independent** — variants are laid out in *key* order, not storage order, so
  reordering them in the dashboard does not reshuffle every user.
- **Decorrelated** — variant choice is hashed with a different salt than rollout
  inclusion. Without that, the users admitted by a 20% rollout would all sit in the
  lowest buckets and therefore all land in the first variant, silently ruining the
  experiment. `test_variant_choice_is_decorrelated_from_rollout_bucket` asserts the
  split still holds *inside* a partial rollout.

Validation lives at the API boundary, not in the engine: weights must sum to 100, keys
must be unique, an `off_variant` and any rule-pinned variant must exist. The engine
itself stays **total** — it never raises — so an SDK evaluating a stale cached ruleset
can't blow up on the hot path. A rule pinning a since-deleted variant degrades to the
weighted split rather than serving nothing.


## The rebuild race

Worth writing up, because it is the bug in this project I would actually want to
talk about — a correctness failure that only exists under concurrency, that no
unit test could have caught, and that silently broke the feature the service
exists to provide.

The cache is a Redis hash with per-flag invalidation. Two writers touch it:

- **A change to one flag** rewrites one field (`HSET`) and publishes an event.
- **A cold-cache rebuild** reads every flag from Postgres and replaces the hash
  wholesale (`DEL` + `HSET` + `EXPIRE`).

Each is correct alone. Interleaved, they are not:

1. A reader finds the cache cold and starts reading the database. With a few
   thousand flags that takes a couple of hundred milliseconds.
2. Mid-read, an operator disables a flag. It commits, and the fresh payload
   lands in the hash.
3. The reader finishes. Its `DEL` wipes that fresh field and republishes the
   **pre-kill** snapshot with a new 300-second TTL.

Nothing is left to invalidate, so the killed flag keeps serving for up to five
minutes. The window is widest exactly when it hurts most: after a Redis restart,
with SDKs bootstrapping, while someone is trying to kill a bad feature.

The fix is a fence. A monotonic `ripcord:ruleset:epoch` counter is bumped on
every change; a rebuild reads it **before** its database query and publishes
inside a `WATCH`/`MULTI`/`EXEC`, so a snapshot that has been overtaken aborts
instead of overwriting. An aborted publish costs nothing — the caller already
holds the mapping it just built and serves from that; the cache is simply cold
for one more request.

Two details that only showed up when I tried to test it:

- `read_epoch` has to return `"0"` rather than `None` for an unset counter,
  because `None` was the "no fence requested" sentinel — which disabled the
  fence precisely in the cold-Redis case the fence exists for.
- My first end-to-end test passed with the fence removed. The flag it created
  had `enabled` at its default of `false`, so the "stale" snapshot already said
  off and there was nothing to serve wrongly. A regression test that passes
  against the bug is worse than no test.

## Observability

- **Structured logs** — one JSON object per line (`{"event":"flag.updated","key":"...","version":3,...}`).
- **Prometheus** — `GET /metrics` exposes request counts + latency and a custom
  `ripcord_flag_evaluations_total{result=...}` counter.

## Load test

`k6 run loadtest/evaluate.js` against `/evaluate` (50 VUs, 20s).

The interesting number is the **A/B**: `/evaluate` used to do a Postgres query per
call, and now reads the Redis ruleset hash. Same k6 script, same seeded flag, same
box — only the data source changed:

| | Postgres per call | Redis `HGET` | Change |
|---|---|---|---|
| Throughput | 321 / 357 req/s | **1,366 / 1,445 req/s** | **~4.1× faster** |
| Latency p95 | 379 / 349 ms | **76 / 71 ms** | **~4.9× lower** |
| Latency p99 | 541 / 533 ms | **87 / 81 ms** | **~6.4× lower** |
| Errors | 0% | 0% | — |

Two runs per configuration, reported individually rather than averaged so the
run-to-run spread is visible.

> **Measured on a 2-vCPU / 7 GB Linux container with Postgres, Redis, the API and k6
> all sharing the same box** — so these absolute numbers are conservative and
> contention-bound. The *ratio* is the claim; re-run both configurations on your own
> hardware before quoting an absolute figure. To reproduce the "before" column, point
> `ripcord/api/evaluate.py` at `services.evaluate_flag` instead of
> `services.evaluate_flag_cached`.

Apps should still prefer the **SDK**, whose local path involves no network hop at all.

## Deployment

- **`docker compose up -d --build`** — the quick local stack.
- **Terraform** (`terraform/`) — the same stack as reproducible infra-as-code via the
  Docker provider (`terraform init && terraform apply`). See [terraform/README.md](terraform/README.md).

## Testing

```bash
pytest -q                  # unit + integration
ruff check .
python scripts/e2e.py      # end-to-end, against a running stack
cd dashboard && npm run build
```

By default `pytest` spins ephemeral Postgres + Redis via **testcontainers**, which
needs a running Docker daemon. To point the suite at databases you already have
(CI service containers, or a machine without Docker):

```bash
RIPCORD_TEST_POSTGRES_URL=postgresql+asyncpg://ripcord:ripcord@localhost:5432/ripcord_test \
RIPCORD_TEST_REDIS_URL=redis://localhost:6379/0 \
pytest -q
```

That is not a way to skip the integration tests — they still run against a real
database, the schema dropped and recreated per test.

**166 tests**, plus a 42-check end-to-end suite. The evaluation engine has dedicated unit
tests for determinism, **monotonicity**, **decorrelation** and rollout **distribution**;
the API and optimistic-locking concurrency run against a real database. Auth is the
best-covered part: the route list comes from the app's own OpenAPI schema, so the
401 check and the full (route × scope) 403 matrix are exhaustive by construction rather
than by maintenance, and a separate test proves the declared scope for each route is the
one that actually works — which is how `/stream` turned out to require `sdk` and not
`flags:read`. The whole suite runs *authenticated*, so a regression in the credentialed
path fails the build.

The SDK's watch loop is driven through a fake HTTP client rather than a socket, so it is
a unit test of the loop, not an integration test — worth saying, because an earlier
version of this paragraph implied otherwise.

`scripts/e2e.py` covers what in-process tests structurally cannot: a real uvicorn
process, a schema built by **Alembic rather than `create_all`**, real HTTP, a real SSE
socket, and the actual Docker image. CI runs it against `docker compose up`.

## Project structure

```
ripcord/
├── ripcord/            # FastAPI backend
│   ├── api/            # routers: flags, evaluate, realtime, insights, keys, health
│   ├── engine.py       # pure evaluation engine (shared with the SDK)
│   ├── auth.py         # API-key hashing, verification, scope enforcement
│   ├── cli.py          # operator CLI: mint-bootstrap / create-key
│   ├── services.py     # flag CRUD + optimistic locking + audit log
│   ├── cache.py        # Redis ruleset cache + pub/sub
│   ├── metrics.py      # Prometheus instrumentation
│   └── sdk/            # installable Python client (local eval, fail-open)
├── dashboard/          # Next.js + Tailwind dashboard
├── migrations/         # Alembic migrations
├── scripts/e2e.py      # end-to-end checks against a running stack
├── loadtest/           # k6 load test
├── terraform/          # infra as code (Docker provider)
├── tests/              # pytest suite
├── Dockerfile          # backend image
└── docker-compose.yml  # full local stack
```

## Limitations / what I'd improve

- **No environments.** Scopes are service-wide: there is no dev/staging/prod separation,
  so one key can change a flag everywhere. This is the biggest real gap — a multi-team
  deployment needs per-environment flag values and per-environment keys, which is a
  data-model change (flag → environment → value) rather than a missing endpoint.
- **No key rotation window** — rotating means minting a new key and revoking the old one;
  there's no overlap period where both are briefly valid.
- **Changing a variant's weight can reassign users near a band boundary.** Inherent to
  cumulative weighted bucketing (LaunchDarkly behaves the same way); avoiding it needs
  per-variant consistent hashing with a much larger constant factor.
- **Single Redis, no cluster story.** The ruleset hash lives on one instance; a real
  deployment would want replication and a documented failover behaviour. The service
  already degrades to the database if Redis is unreachable, so this is availability
  headroom rather than correctness.
- **The dashboard stores its key in `localStorage`** — appropriate for an operator tool
  on a separate origin, but a real multi-user deployment wants SSO and per-user identity
  in the audit log rather than per-key.
- **The SSE credential travels in the query string.** Browsers' `EventSource` cannot set
  headers, so `/stream` is the one route that accepts `?api_key=`. Uvicorn logs the whole
  request line, so a dashboard tab was writing a live operator key into the access log on
  every reconnect; there is now a redaction filter on `uvicorn.access`, which stops the
  leak but is not the real fix. The real fix is a short-lived, single-scope stream token
  minted from the real key, so what appears in a URL is a credential that expires in
  seconds and can do nothing else.
- **Verified keys are cached in-process for 5 seconds.** `/evaluate` answers from Redis,
  but every request still paid an indexed Postgres lookup to *authenticate*, so "no
  database on the hot path" was only true of the flag. The cost of the cache is that
  revocation is immediate on the process that handled it and takes up to
  `AUTH_CACHE_SECONDS` to reach others; set it to `0` to disable. Nothing is cached for a
  key that fails verification, so a stale entry can only keep a valid key working
  slightly too long — it can never accept an invalid one.
- **No rate limiting.** The 256-bit secrets make online brute force a non-issue, but
  `/keys` and `/evaluate` are unthrottled, so a single client can still saturate the
  service. A real deployment wants per-key limits at the edge.

## License

[MIT](LICENSE)
