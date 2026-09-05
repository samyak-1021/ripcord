"""Application configuration, loaded from environment variables / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the Ripcord service.

    Field values are read from environment variables (case-insensitive) or a
    local `.env` file. See `.env.example` for the full list. Unrelated env
    vars are ignored so the app is happy running inside CI or a container.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Human-readable service name, surfaced by the health check and logs.
    app_name: str = "ripcord"

    # Postgres connection string (async driver). Wired up in Phase 1.
    database_url: str = "postgresql+asyncpg://ripcord:ripcord@localhost:5432/ripcord"

    # Redis connection string (cache + pub/sub). Wired up in Phase 4.
    redis_url: str = "redis://localhost:6379/0"


# A single shared instance imported across the app (settings are read once).
settings = Settings()
