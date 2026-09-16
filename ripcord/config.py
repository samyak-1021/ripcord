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

    # Postgres connection string (async driver).
    database_url: str = "postgresql+asyncpg://ripcord:ripcord@localhost:5432/ripcord"

    # Redis connection string (ruleset cache + pub/sub).
    redis_url: str = "redis://localhost:6379/0"

    # Browser origins allowed to call the API (the dashboard). Override via the
    # CORS_ORIGINS env var (a JSON list) in other environments.
    cors_origins: list[str] = ["http://localhost:3000"]

    # API-key auth on the management + SDK endpoints. Defaults to ON: a flag
    # service with an open write API is a production incident waiting to happen,
    # so turning it off has to be a deliberate act (and logs a loud warning).
    auth_enabled: bool = True

    # Seconds to hold a verified key in process memory. Small on purpose: it is
    # also the worst-case delay before a revocation takes effect on a server
    # other than the one that processed it. 0 disables the cache entirely,
    # which is what the auth tests use so revocation is observable immediately.
    auth_cache_seconds: float = 5.0

    # A single admin key accepted without a database lookup. This exists to
    # solve the bootstrap problem — you need a key to mint the first key — and
    # for ephemeral environments like CI. Leave it unset in production once a
    # real admin key has been created.
    bootstrap_admin_key: str | None = None


# A single shared instance imported across the app (settings are read once).
settings = Settings()
