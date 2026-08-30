import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api import auth, findings, github, projects, reports, scans
from app.config import get_settings
from app.logging import configure_logging
from app.observability import (
    CONTENT_TYPE_LATEST,
    PrometheusMiddleware,
    render_metrics,
    set_metrics_redis,
)
from app.rate_limit import limiter, rate_limit_exceeded_handler
from app.utils.queue import ArqQueue, set_queue
from app.utils.storage import GcsStorage, LocalStorage, S3Storage, Storage, set_storage

settings = get_settings()

# Before the app is constructed, so anything FastAPI or uvicorn logs during
# startup is already going through the JSON handler.
configure_logging(settings.log_level)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Connect the real queue and storage while the app is serving.

    Tests never reach this: httpx's ASGITransport does not run lifespan events,
    so the suite keeps the in-memory queue and the refusing storage, and needs
    no Redis. Which also means a broken Redis connection shows up when the app
    boots, not in CI.

    Storage is installed here rather than in the worker because reports are
    rendered on demand by the API, not at scan time — the worker never writes
    one and has no reason to hold a bucket client.
    """
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    set_queue(ArqQueue(pool))
    set_storage(_build_storage())
    # Same pool ArqQueue uses — GET /metrics reads arq's queue length and the
    # worker's job-outcome counters off it, both plain Redis reads.
    set_metrics_redis(pool)
    try:
        yield
    finally:
        await pool.aclose()


def _build_storage() -> Storage:
    """A bucket if one is configured, the filesystem otherwise.

    The bucket wins when both are set. STORAGE_DIR has a default and is baked
    into the image, so a deployment configuring a bucket would otherwise have to
    remember to unset something to make it take effect — and the failure would
    be silent: reports written to a container filesystem that is discarded at
    scale-to-zero, with nothing in the logs to say so.

    Constructed eagerly, so a missing SDK or unreadable credentials stop the app
    from starting rather than surfacing on the first download somebody attempts.
    """
    if settings.storage_bucket and settings.storage_provider == "s3":
        logger.info(
            "Storing reports in S3-compatible storage",
            extra={"bucket": settings.storage_bucket, "endpoint": settings.storage_endpoint_url},
        )
        return S3Storage(
            settings.storage_bucket,
            endpoint_url=settings.storage_endpoint_url,
            region=settings.storage_region,
        )

    if settings.storage_bucket:
        logger.info("Storing reports in Cloud Storage", extra={"bucket": settings.storage_bucket})
        return GcsStorage(settings.storage_bucket)

    logger.info(
        "Storing reports on the local filesystem", extra={"path": str(settings.storage_dir)}
    )
    return LocalStorage(settings.storage_dir)


# Routes are mounted at the root, not under /api. The frontend's dev server
# proxies /api here and strips that prefix before forwarding, so the backend
# only ever sees /health, /auth/login, /projects, and so on.
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)


# slowapi reaches the limiter through app.state, so the decorators on the auth
# routes cannot work without this assignment.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Required for the auth cookie to cross origins at all. Note that browsers
    # reject allow_credentials alongside a "*" origin, which is why cors_origins
    # is an explicit list rather than a wildcard.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added after CORS, which Starlette therefore wraps outermost — a CORS
# preflight never needs timing, and this way it never sees one.
app.add_middleware(PrometheusMiddleware)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(scans.router)
app.include_router(findings.router)
app.include_router(reports.router)
app.include_router(github.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe.

    Deliberately touches nothing external — it answers "is this process up?",
    not "are its dependencies up?". Conflating the two makes a health check that
    fails for reasons the process cannot act on.
    """
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape target. Internal-only by construction — see
    app/observability.py's module docstring and frontend/Caddyfile, which
    proxies /api/* and nothing else.
    """
    return Response(content=await render_metrics(), media_type=CONTENT_TYPE_LATEST)
