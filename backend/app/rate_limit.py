"""Request rate limiting.

Scoped to the auth endpoints on purpose. A global default limit is tempting but
would collide with how this API is actually used: the client polls
GET /scans/{id} every three seconds while a scan runs, so a single user with two
tabs open legitimately makes ~40 requests a minute. A ceiling low enough to be
useful against abuse would break polling, and one high enough not to would not
be worth having. Brute-forcing credentials is the threat that matters here, and
that is what is limited.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings

settings = get_settings()

limiter = Limiter(
    # Buckets by client IP. Behind a proxy this is only correct if the ASGI
    # server is told to trust forwarded headers — see the note in the Dockerfile.
    # Reading X-Forwarded-For here instead would be worse: the header is
    # attacker-controlled, so anyone could rotate it and bypass the limit
    # entirely.
    key_func=get_remote_address,
    storage_uri=settings.rate_limit_storage_uri,
    enabled=settings.rate_limit_enabled,
    # Emit X-RateLimit-* headers so a client can see how much budget is left
    # rather than discovering the limit by hitting it.
    headers_enabled=True,
)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return 429 in the error shape the rest of the API uses.

    slowapi's built-in handler responds with {"error": ...}. The client reads
    {"detail": ...} and falls back to the bare status text for anything else, so
    the default would surface "Too Many Requests" instead of saying how long to
    wait.
    """
    response = JSONResponse(
        status_code=429,
        content={"detail": f"Too many requests. Limit is {exc.detail}. Try again shortly."},
    )
    # Preserves Retry-After and the X-RateLimit-* headers the default handler
    # would have attached.
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
