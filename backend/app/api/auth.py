import secrets
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import COOKIE_NAME, CurrentUser, DbSession
from app.auth.jwt import create_access_token
from app.auth.security import hash_password, verify_password
from app.config import get_settings
from app.models import User
from app.rate_limit import limiter
from app.schemas.user import Credentials, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


_decoy_hash_cache: str | None = None


async def _decoy_hash() -> str:
    """A hash of a password nobody knows, for timing purposes only.

    Computed once, from random input, so it can never be matched. Cached by hand
    rather than with functools.cache, which cannot memoise a coroutine — it would
    store the awaitable and every call after the first would fail on a second
    await.
    """
    global _decoy_hash_cache
    if _decoy_hash_cache is None:
        _decoy_hash_cache = await hash_password(secrets.token_urlsafe(32))
    return _decoy_hash_cache


def _issue_auth_cookie(response: Response, user_id: uuid.UUID) -> None:
    """Send the JWT as an httpOnly cookie rather than a field in the body.

    The token never reaches JavaScript, so an XSS bug on the frontend cannot
    read it — the point of the choice, given this product assesses other
    people's security.
    """
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_access_token(user_id),
        httponly=True,
        secure=settings.cookie_secure,
        # Lax still sends the cookie on top-level navigation to the app, while
        # withholding it from cross-site form posts and subrequests.
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    """Expire the session cookie.

    Every attribute that takes part in cookie matching has to repeat what
    `_issue_auth_cookie` set — a delete with a different `path` writes a second
    cookie instead of removing the first, and the browser keeps sending the
    original. `path="/"` is the one that matters here; the rest are set for the
    same reason the issuing side sets them, so the two cannot drift apart
    unnoticed.
    """
    settings = get_settings()
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
# Limits account creation from one address. Looser than login because a
# legitimate person may genuinely retry after a validation error.
@limiter.limit(get_settings().signup_rate_limit)
async def signup(
    request: Request, credentials: Credentials, response: Response, db: DbSession
) -> User:
    existing = await db.scalar(select(User).where(User.email == credentials.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    user = User(
        email=credentials.email,
        password_hash=await hash_password(credentials.password),
    )
    db.add(user)

    try:
        await db.commit()
    except IntegrityError as exc:
        # The check above loses a race against a simultaneous signup with the
        # same address. The unique index is what actually enforces this; the
        # check exists only to give the common case a better message.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        ) from exc

    _issue_auth_cookie(response, user.id)
    return user


@router.get("/me", response_model=UserRead)
# Deliberately not rate limited, unlike the two endpoints below. Those exist to
# be ground against — this one is a primary-key lookup behind a signature check,
# and it is the first call the app makes on every page load. A limit here would
# throttle ordinary refreshing before it slowed an attacker who, by definition,
# already has a valid cookie.
async def me(user: CurrentUser) -> User:
    """Who the current cookie belongs to.

    The cookie is httpOnly, so after a refresh the frontend has no way to answer
    this for itself; without this endpoint the only way to discover a dead
    session is to make some other request and be told 401 — which meant every
    returning visitor saw a page skeleton before being bounced to login.

    Returns the same 401 as every other authenticated route, from the same
    dependency, so a missing cookie, a bad signature and a deleted account stay
    indistinguishable.
    """
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Expire the session cookie. Always succeeds.

    Not behind `CurrentUser`, on purpose. Logging out with an expired or
    malformed cookie would then answer 401 and leave that cookie in place — the
    one situation where the caller most wants it gone. There is nothing to
    protect either: the only effect is on the caller's own browser, and the
    server holds no session state to destroy, because the JWT is the session.
    """
    _clear_auth_cookie(response)


@router.post("/login", response_model=UserRead)
# The endpoint an attacker would grind against. bcrypt already makes each guess
# cost ~200ms, but nothing stopped sustained attempts before this.
@limiter.limit(get_settings().login_rate_limit)
async def login(
    request: Request, credentials: Credentials, response: Response, db: DbSession
) -> User:
    user = await db.scalar(select(User).where(User.email == credentials.email))

    # Verify against a decoy when the account does not exist, so that a missing
    # address and a wrong password take the same time. Returning early instead
    # would make login a user-enumeration oracle: bcrypt is deliberately slow,
    # and skipping it is measurable from outside.
    stored_hash = user.password_hash if user is not None else await _decoy_hash()
    password_matches = await verify_password(credentials.password, stored_hash)

    if user is None or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    _issue_auth_cookie(response, user.id)
    return user
