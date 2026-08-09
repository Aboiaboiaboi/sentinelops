from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api import auth, findings, github, projects, scans
from app.config import get_settings
from app.logging import configure_logging
from app.rate_limit import limiter, rate_limit_exceeded_handler
from app.utils.queue import ArqQueue, set_queue
from app.utils.storage import LocalStorage, set_storage

settings = get_settings()

# Before the app is constructed, so anything FastAPI or uvicorn logs during
# startup is already going through the JSON handler.
configure_logging(settings.log_level)


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
    set_storage(LocalStorage(settings.storage_dir))
    try:
        yield
    finally:
        await pool.aclose()


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

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(scans.router)
app.include_router(findings.router)
app.include_router(github.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe.

    Deliberately touches nothing external — it answers "is this process up?",
    not "are its dependencies up?". Conflating the two makes a health check that
    fails for reasons the process cannot act on.
    """
    return {"status": "ok"}
