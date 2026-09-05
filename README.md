# Ripcord

[![CI](https://github.com/samyak-1021/ripcord/actions/workflows/ci.yml/badge.svg)](https://github.com/samyak-1021/ripcord/actions/workflows/ci.yml)

A self-hostable **feature-flag & gradual-rollout service**. Toggle features on/off,
target users by attributes, roll them out to a percentage of traffic, and **pull the
ripcord to kill a bad feature instantly** — no redeploy.

> 🚧 **Status:** in active development, built in phases (see [Build plan](#build-plan)).

## Why

Shipping a feature to 100% of users on day one is risky. Real teams release to 5% first,
target by country/plan, watch the metrics, and kill anything that misbehaves — all without
a code deploy. Ripcord is a small, honest, self-hostable version of that (think a
LaunchDarkly-lite you can run yourself).

## Tech stack

- **API:** FastAPI (async) + Pydantic v2
- **Data:** PostgreSQL + async SQLAlchemy + Alembic
- **Cache / real-time:** Redis (cache + pub/sub), Server-Sent Events
- **SDK:** installable Python client (evaluates flags locally, fails open)
- **Dashboard:** Next.js + React
- **Quality:** pytest + testcontainers, k6 load test, Ruff
- **Ops:** Docker, GitHub Actions CI/CD, Terraform

## Quickstart

```bash
# 1. Start Postgres + Redis
docker compose up -d

# 2. Create a virtualenv and install (with dev tooling)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Run the API
uvicorn ripcord.main:app --reload

# 4. Check it's alive
curl localhost:8000/health
# -> {"status":"ok","service":"ripcord","version":"0.1.0"}
```

Run the test suite and linter:

```bash
pytest -q
ruff check .
```

## Build plan

- [x] **Phase 0** — Scaffold & CI
- [ ] **Phase 1** — Data layer (models + migrations)
- [ ] **Phase 2** — Flag management API
- [ ] **Phase 3** — Evaluation engine (consistent-hashing rollout + rule DSL)
- [ ] **Phase 4** — Real-time propagation (Redis pub/sub + SSE)
- [ ] **Phase 5** — Python SDK + observability + load test
- [ ] **Phase 6** — Dashboard, deploy & docs

## Project structure

```
ripcord/
├── ripcord/            # FastAPI backend package
│   ├── api/            # HTTP routers
│   ├── config.py       # env-driven settings
│   └── main.py         # app factory + entrypoint
├── tests/              # pytest suite
├── docker-compose.yml  # local Postgres + Redis
└── .github/workflows/  # CI
```

## License

[MIT](LICENSE)
