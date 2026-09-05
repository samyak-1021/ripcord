"""Shared pytest fixtures: an ephemeral Postgres and an HTTP client."""

from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

import ripcord.models  # noqa: F401  # imported so models register on Base.metadata
from ripcord.db import Base, get_session
from ripcord.main import create_app


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    """Start one throwaway Postgres container for the whole test session."""
    with PostgresContainer("postgres:16") as postgres:
        # Build an asyncpg URL from the container's details (the built-in
        # helper returns a psycopg2 URL, which our async engine can't use).
        host = postgres.get_container_host_ip()
        port = postgres.get_exposed_port(5432)
        url = (
            f"postgresql+asyncpg://{postgres.username}:{postgres.password}"
            f"@{host}:{port}/{postgres.dbname}"
        )
        yield url


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
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client whose app uses the per-test database session."""
    app = create_app()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
