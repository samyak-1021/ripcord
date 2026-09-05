"""Database engine, async session factory, and the declarative base."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ripcord.config import settings


class Base(DeclarativeBase):
    """Declarative base that every ORM model inherits from."""


# One engine per process. `create_async_engine` is lazy — it opens no
# connection until first use, so importing this module is always safe.
engine = create_async_engine(settings.database_url, pool_pre_ping=True)

# Session factory. `expire_on_commit=False` lets us read attributes after a
# commit without triggering another (awaited) database round-trip.
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields one database session per request."""
    async with SessionFactory() as session:
        yield session
