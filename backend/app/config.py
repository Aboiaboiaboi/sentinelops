from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is read once per process rather than per request."""
    return Settings()
