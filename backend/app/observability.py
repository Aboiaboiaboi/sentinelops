"""Prometheus metrics for the API process.

GET /metrics (app/main.py) is the only reader of anything in this module.
Deliberately top-level like /health, not under /api/* — Caddy's
handle_path /api/* never proxies it publicly, and Prometheus reaches it
directly at backend:8000/metrics over the Compose network only
(deploy/compose/docker-compose.observability.yml).

Three kinds of number, each solving a different visibility gap:
- Request rate/latency, from the ASGI middleware below, in-process.
- Scan queue depth, read from arq's own Redis queue at scrape time — this
  process never publishes to it, only reads its length.
- Scan job outcomes, written by the *worker* process
  (app/workers/scan_tasks.py) into plain Redis counters, because the API and
  worker are separate processes and an in-process prometheus_client Counter
  in one is invisible to the other's /metrics. Mirrored into a Counter here
  at scrape time — the documented prometheus_client pattern for a counter
  whose real state lives elsewhere.
"""

import time
from typing import TYPE_CHECKING

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from arq import ArqRedis

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests handled",
    ["method", "route", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "route"],
)
SCAN_QUEUE_DEPTH = Gauge(
    "arq_queue_depth",
    "Number of scan jobs currently waiting in the arq queue",
)
SCAN_JOBS_TOTAL = Counter(
    "scan_jobs_total",
    "Scan jobs completed, by outcome",
    ["outcome"],
)

# arq's own default queue key (WorkerSettings sets no explicit queue name,
# so this is the one it actually uses — see app/workers/settings.py).
_ARQ_QUEUE_KEY = "arq:queue"

# Written by the worker (app/workers/scan_tasks.py._record_job_outcome),
# read here. Plain string constants, not app.config settings — matching how
# app/rate_limit.py's own Redis keys aren't configurable either.
_JOB_OUTCOME_KEYS = {
    "success": "sentinelops:jobs:success",
    "failure": "sentinelops:jobs:failure",
}

_redis: "ArqRedis | None" = None


def set_metrics_redis(redis: "ArqRedis") -> None:
    """Called once from main.py's lifespan, with the same pool ArqQueue uses.

    No second Redis connection: arq's pool already speaks every command
    used here (ZCARD, GET) — it is a redis.asyncio.Redis subclass, not a
    narrower arq-specific client.
    """
    global _redis
    _redis = redis


class PrometheusMiddleware:
    """Plain ASGI middleware, not BaseHTTPMiddleware.

    BaseHTTPMiddleware buffers the whole response to let you inspect it,
    which defeats streaming responses for every request in the app just to
    time this one thing. Wrapping `send` instead costs nothing extra.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        start = time.perf_counter()
        await self.app(scope, receive, send_wrapper)
        duration = time.perf_counter() - start

        # Starlette sets scope["route"] during dispatch, which has already
        # happened by the time the inner app() call above returns. Falls
        # back to "unmatched" for a 404 — the cardinality guard: a raw path
        # like /scans/<uuid> would otherwise mint a new label value per scan.
        route = scope.get("route")
        route_path = route.path if route is not None else "unmatched"

        REQUEST_COUNT.labels(method=method, route=route_path, status=str(status_code)).inc()
        REQUEST_LATENCY.labels(method=method, route=route_path).observe(duration)


async def _read_queue_depth() -> int:
    if _redis is None:
        return 0
    return await _redis.zcard(_ARQ_QUEUE_KEY)


async def _read_job_outcomes() -> dict[str, int]:
    if _redis is None:
        return dict.fromkeys(_JOB_OUTCOME_KEYS, 0)
    counts = {}
    for outcome, key in _JOB_OUTCOME_KEYS.items():
        raw = await _redis.get(key)
        counts[outcome] = int(raw) if raw else 0
    return counts


async def render_metrics() -> bytes:
    """The full text body for GET /metrics.

    Refreshes the two Redis-backed series just before rendering — cheap at
    Prometheus's 15s scrape interval, and it means SCAN_QUEUE_DEPTH and
    SCAN_JOBS_TOTAL are never more than one scrape stale.
    """
    SCAN_QUEUE_DEPTH.set(await _read_queue_depth())
    for outcome, count in (await _read_job_outcomes()).items():
        # ._value.set(...), not .inc(...): this mirrors an absolute count
        # already tracked in Redis, not an in-process increment — see the
        # module docstring on why job outcomes can't be a normal Counter here.
        SCAN_JOBS_TOTAL.labels(outcome=outcome)._value.set(count)  # noqa: SLF001
    return generate_latest()


__all__ = [
    "CONTENT_TYPE_LATEST",
    "PrometheusMiddleware",
    "render_metrics",
    "set_metrics_redis",
]
