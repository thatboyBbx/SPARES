"""
Application configuration.

All runtime configuration is loaded from environment variables (or a .env
file in local development) via pydantic-settings. Nothing here should be
hardcoded per-environment — Docker Compose / CI / production all inject
different values for the same variable names.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


PILOT_PASSWORD = "pilot123"


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

    # --- Notifications ---
    notifications_enabled: bool = True  # kill switch; leave on but unconfigured channels safely no-op
    firebase_credentials_json: str | None = None  # path to (or raw JSON of) a Firebase service account
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_sender: str | None = None
    smtp_recipient: str | None = None

    def assert_deployable(self) -> None:
        """Reject settings that are safe for a laptop but unsafe for production."""
        if self.environment != "production":
            return
        problems: list[str] = []
        if self.debug:
            problems.append("DEBUG must be false")
        if self.jwt_secret_key in {"change-me-in-production", "local-dev-secret-do-not-use-in-production"} or len(self.jwt_secret_key) < 32:
            problems.append("JWT_SECRET_KEY must be a unique value of at least 32 characters")
        if "localhost" in self.database_url or "@postgres:" in self.database_url:
            problems.append("DATABASE_URL must point to the production database")
        if not self.allowed_origins or any("localhost" in origin for origin in self.allowed_origins):
            problems.append("ALLOWED_ORIGINS must contain only deployed application origins")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance. Cached because Settings() re-reads
    the environment on every instantiation, and it's read frequently
    (every request that depends on it).
    """
    return Settings()
