"""Shared route dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_access_token
from app.database.session import SessionLocal
from app.models import User

# The frontend never reads this — the cookie is httpOnly and invisible to JS —
# so the name only has to stay stable between the two endpoints that set it and
# the dependency that reads it.
COOKIE_NAME = "token"


async def get_db() -> AsyncIterator[AsyncSession]:
    """One session per request, closed when the request ends.

    Deliberately does not commit. A route that changed something commits it
    explicitly, so a read-only request cannot accidentally flush a half-built
    object written by a failed one.
    """
    async with SessionLocal() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


def _unauthorized() -> HTTPException:
    """One error for every authentication failure.

    Missing cookie, bad signature, expired token, and deleted user all produce
    the same response. Distinguishing them would tell an attacker which part to
    work on, and the client cannot act differently on any of them anyway.
    """
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def get_current_user(
    db: DbSession,
    token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> User:
    if token is None:
        raise _unauthorized()

    user_id = decode_access_token(token)
    if user_id is None:
        raise _unauthorized()

    # A token can outlive the account it names. Verifying the user still exists
    # is what stops a deleted account's cookie from continuing to work until it
    # expires on its own.
    user = await db.get(User, user_id)
    if user is None:
        raise _unauthorized()

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
