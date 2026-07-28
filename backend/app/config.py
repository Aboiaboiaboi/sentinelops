from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Public knowledge by definition — it is in the repository. Usable in
# development, rejected in production by the validator below.
DEV_SECRET_KEY = "dev-only-insecure-secret-change-me"

# Matches the SHA-256 output size, per RFC 7518 §3.2.
MIN_SECRET_KEY_BYTES = 32


class Settings(BaseSettings):
    """Application configuration, read from the environment or a local .env file.

    Every setting carries a development-safe default so the app starts with no
    .env at all. Anything that must differ in production (and cannot be guessed)
    gets added here as it becomes necessary, not preemptively.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SentinelOps"
    environment: Literal["development", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Defaults line up with docker-compose.yml, so a fresh clone connects with no
    # .env at all. The +asyncpg driver is not optional — SQLAlchemy picks the
    # sync driver without it and every await fails at runtime.
    database_url: str = "postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops"

    secret_key: str = DEV_SECRET_KEY
    jwt_algorithm: str = "HS256"
    # Seven days. There is no refresh-token flow and no session endpoint, so a
    # short expiry would log people out mid-scan with no way to renew quietly.
    access_token_expire_minutes: int = 60 * 24 * 7

    @model_validator(mode="after")
    def _validate_production_secret(self) -> "Settings":
        if self.environment != "production":
            return self

        if self.secret_key == DEV_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be set explicitly when ENVIRONMENT=production. "
                "The development default is in the repository, so anyone could "
                "mint valid tokens with it."
            )
        # RFC 7518 §3.2: an HMAC key for SHA-256 should be at least as long as
        # the hash output. PyJWT only warns about a shorter one, and a warning
        # in a log nobody reads is not a control.
        if len(self.secret_key.encode("utf-8")) < MIN_SECRET_KEY_BYTES:
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_BYTES} bytes in production; "
                f"got {len(self.secret_key.encode('utf-8'))}."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is read once per process rather than per request."""
    return Settings()
