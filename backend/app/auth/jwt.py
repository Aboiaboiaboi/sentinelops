"""Access token creation and verification.

Uses PyJWT rather than python-jose, which the spec listed as the alternative:
python-jose is unmaintained and has had signature-verification CVEs, which is a
poor property for the component that decides who a request belongs to.

This module knows nothing about users or the database — it converts between a
user id and a signed string, and nothing more.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    issued_at = datetime.now(UTC)

    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> uuid.UUID | None:
    """Return the user id a token belongs to, or None if it is not usable.

    None covers every failure equally — bad signature, expired, malformed,
    missing subject. The caller turns any of them into the same 401, and
    reporting which one it was would tell an attacker what to fix.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            # Naming the accepted algorithm is what makes this safe. Without it
            # PyJWT would honour the token's own `alg` header, and a token
            # claiming `alg: none` would verify against no signature at all.
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.InvalidTokenError:
        return None

    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None

    try:
        return uuid.UUID(subject)
    except ValueError:
        return None
