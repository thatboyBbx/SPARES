"""
Application configuration.

All runtime configuration is loaded from environment variables (or a .env
file in local development) via pydantic-settings. Nothing here should be
hardcoded per-environment — Docker Compose / CI / production all inject
different values for the same variable names.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Typed application settings. Each field maps to an environment variable
    of the same name (case-insensitive).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App metadata ---
    app_name: str = "SPOP API"
    environment: str = "development"  # development | staging | production
    debug: bool = True

    # --- Database ---
    database_url: str = "postgresql+psycopg://spop:spop@localhost:5432/spop"

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth ---
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # --- CORS ---
    allowed_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance. Cached because Settings() re-reads
    the environment on every instantiation, and it's read frequently
    (every request that depends on it).
    """
    return Settings()
