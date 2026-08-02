from functools import lru_cache
from pathlib import Path
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

    # Where generated reports are written in development. Gitignored, and
    # replaced by a bucket once there is somewhere to deploy to.
    storage_dir: Path = Path("reports")

    # Job queue. Matches docker-compose.yml; database 0 is arq's, and the rate
    # limiter would take a different one if it ever moves off memory://.
    redis_url: str = "redis://localhost:6379/0"

    # Where repositories are cloned before scanning. Gitignored. Each scan gets
    # a disposable subdirectory that is removed whether or not it succeeded.
    clone_root: Path = Path("repos")
    # These bound what an arbitrary user-submitted repository can cost us. The
    # timeout is the effective limit on download size, since the byte cap can
    # only be enforced once the clone has finished.
    clone_timeout_seconds: int = 120
    clone_max_bytes: int = 500_000_000
    clone_max_files: int = 50_000

    # Container isolation for the security tools. Off by default: with no
    # sandbox the checks report themselves as errored, which is honest, where a
    # default of "run them unisolated" would execute third-party binaries
    # against a hostile repository on whatever machine happens to start the
    # worker.
    sandbox_enabled: bool = False
    # The named volume holding clones, when the worker is itself a container.
    # Empty means the worker runs on the host and the clone can be bind-mounted
    # by its real path. See DockerSandbox — getting this wrong makes every tool
    # scan an empty directory and report a clean repository.
    sandbox_volume: str = ""
    # The named volume holding Trivy's vulnerability database and Semgrep's
    # rules, mounted read-only into each scan container. The sandbox has no
    # network, so a tool that needs data from the internet gets it from here or
    # reports errored. Populated by the warm services in docker-compose.yml.
    sandbox_cache_volume: str = ""
    sandbox_timeout_seconds: int = 300
    sandbox_memory_mb: int = 512
    # How many tool containers this worker may run at once, across every scan it
    # is handling. Without it the real ceiling is max_jobs × tools-per-scanner,
    # a number set in two files that neither one states — five scans running
    # three tools each is fifteen containers, and at the limit above that is
    # more memory than a developer's Docker VM has.
    #
    # Six is two scans' worth of tools, and it is affordable because measurement
    # said so: peaks are Semgrep 271 MiB, Gitleaks 58, Trivy 39, so six
    # containers cost well under a gigabyte in practice against the 3 GB their
    # limits allow. A limit is not a reservation.
    #
    # Queueing past this is cheap rather than dangerous. On a 2,500-file
    # repository the three tools cost ~34 container-seconds together, so even
    # five scans contending for these slots wait tens of seconds against the
    # 300s ceiling in _slot. Raising it buys throughput, not correctness.
    sandbox_max_concurrent: int = 6

    # Only consulted when the browser talks to the API cross-origin. Local dev
    # goes through the frontend's /api proxy, which is same-origin, so CORS never
    # enters the picture there. A wildcard is not an option: the spec sends
    # credentials, and browsers reject "*" alongside them.
    cors_origins: list[str] = ["http://localhost:5173"]

    # Rate limiting. Only the auth endpoints are limited — see rate_limit.py for
    # why there is deliberately no global default.
    rate_limit_enabled: bool = True
    # in-process counters by default, so local dev needs no broker. A deployment
    # running more than one replica must point this at Redis, otherwise each
    # replica enforces the limit separately and the real ceiling is N times what
    # was configured.
    rate_limit_storage_uri: str = "memory://"
    login_rate_limit: str = "10/minute"
    signup_rate_limit: str = "5/minute"

    # GitHub App credentials for private-repository access. Both empty by
    # default: the feature simply reports itself as unconfigured, and public
    # repositories keep working without a GitHub account of any kind.
    #
    # The private key is supplied base64-encoded because it is a multi-line
    # PEM, and multi-line values do not survive .env files or most secret
    # managers' environment injection. The exact encode command is documented
    # in .env.example beside the variable.
    #
    # In production this belongs in a secret manager, not a committed file —
    # it can mint access to every installed user's repositories.
    github_app_client_id: str = ""
    github_app_private_key_b64: str = ""
    # The App's URL slug, for the install redirect:
    # https://github.com/apps/<slug>/installations/new
    github_app_slug: str = ""

    # Where GitHub's post-install redirect sends the browser back to. Separate
    # from cors_origins because this is a navigation target, not an allowed
    # caller.
    frontend_url: str = "http://localhost:5173"

    secret_key: str = DEV_SECRET_KEY
    jwt_algorithm: str = "HS256"
    # Seven days. There is no refresh-token flow and no session endpoint, so a
    # short expiry would log people out mid-scan with no way to renew quietly.
    access_token_expire_minutes: int = 60 * 24 * 7

    @property
    def cookie_secure(self) -> bool:
        """Whether the auth cookie is restricted to HTTPS.

        Derived rather than configured. A Secure cookie is dropped by the browser
        over plain http, so hardcoding it on would make local login silently fail
        to persist; hardcoding it off would ship an auth cookie over HTTP in
        production. Tying it to the environment is correct in both cases.
        """
        return self.environment == "production"

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
