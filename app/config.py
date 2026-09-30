"""App settings, read from environment variables (or a .env file)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the project runs with zero setup.
    # Docker / production use PostgreSQL via DATABASE_URL.
    database_url: str = "sqlite:///./eve.db"

    jwt_secret: str = "dev-only-jwt-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Shared secret between us and the (simulated) payment provider.
    # Used to verify that webhook calls really come from the provider.
    webhook_secret: str = "dev-webhook-secret-change-me"

    # Optional. Without Redis the app still works: caching is skipped and
    # rate limits are counted in memory (fine for a single server).
    redis_url: str | None = None
    cache_ttl_seconds: int = 300


settings = Settings()
