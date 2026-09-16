"""Shared pytest fixtures: ephemeral Postgres + Redis, and an HTTP client."""

import os
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
import redis.asyncio as redis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

import ripcord.models  # noqa: F401  # imported so models register on Base.metadata
from ripcord import auth
from ripcord.cache import get_redis
from ripcord.db import Base, get_session
from ripcord.main import create_app
from ripcord.models import ApiKey


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    """Start one throwaway Postgres container for the whole test session.

    ``RIPCORD_TEST_POSTGRES_URL`` points the suite at an already-running
    Postgres instead. That is not a way to skip the integration tests — they
    still run against a real database — it just avoids nesting Docker where an
    outer system already provides one (CI service containers, a dev box with no
    Docker daemon). The schema is dropped and recreated per test either way.
    """
    external = os.environ.get("RIPCORD_TEST_POSTGRES_URL")
    if external:
        yield external
        return
    with PostgresContainer("postgres:16") as postgres:
        host = postgres.get_container_host_ip()
        port = postgres.get_exposed_port(5432)
        yield (
            f"postgresql+asyncpg://{postgres.username}:{postgres.password}"
            f"@{host}:{port}/{postgres.dbname}"
        )


@pytest.fixture(scope="session")
def redis_url() -> Generator[str, None, None]:
    """Start one throwaway Redis container for the whole test session.

    ``RIPCORD_TEST_REDIS_URL`` points the suite at an already-running Redis —
    see ``postgres_url`` for why. The database is flushed per test either way.
    """
    external = os.environ.get("RIPCORD_TEST_REDIS_URL")
    if external:
        yield external
        return
    with RedisContainer("redis:7") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def session(postgres_url: str) -> AsyncGenerator[AsyncSession, None]:
    """Yield a clean session, recreating the schema fresh for each test."""
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session

    await engine.dispose()


@pytest_asyncio.fixture
async def redis_client(redis_url: str) -> AsyncGenerator[redis.Redis, None]:
    """Yield a Redis client bound to the test container, flushed per test."""
    client = redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def make_key(session: AsyncSession):
    """Factory that mints a real API key row and returns its plaintext value.

    Tests exercise the same code path operators do — a key is only ever valid
    because its digest is in the database, never because a test bypassed auth.
    """

    async def _make(*scopes: str, name: str = "test-key") -> str:
        full_key, key_id, secret_hash = auth.generate_key()
        session.add(
            ApiKey(
                key_id=key_id,
                secret_hash=secret_hash,
                name=name,
                scopes=list(scopes),
            )
        )
        await session.commit()
        return full_key

    return _make


@pytest_asyncio.fixture
async def app(session: AsyncSession, redis_client: redis.Redis):
    """The FastAPI app wired to the per-test Postgres and Redis."""
    application = create_app()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    application.dependency_overrides[get_session] = _use_test_session
    application.dependency_overrides[get_redis] = lambda: redis_client
    yield application
    application.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_key_cache() -> Generator[None, None, None]:
    """Start every test with an empty verification cache.

    The cache is per-process and the whole suite shares one, so without this a
    key minted in one test could still authenticate in the next — which would
    make the revocation tests pass for the wrong reason.
    """
    auth.invalidate_key_cache()
    yield
    auth.invalidate_key_cache()


@pytest_asyncio.fixture
async def anon_client(app) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client that sends no credentials — for asserting 401s."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def client(app, make_key) -> AsyncGenerator[AsyncClient, None]:
    """The default client: authenticated with an admin key.

    Deliberately a *separate* AsyncClient from ``anon_client`` — sharing one and
    swapping its header would let a test that re-points the anonymous client
    silently re-point the admin client too.

    Every pre-existing test keeps working unchanged while auth is genuinely
    enabled underneath, so the suite now covers the authenticated path rather
    than an unauthenticated one.
    """
    key = await make_key(auth.SCOPE_ADMIN, name="test-admin")
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as http_client:
        yield http_client
