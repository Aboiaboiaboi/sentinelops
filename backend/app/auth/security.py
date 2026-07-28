"""Password hashing.

Uses the `bcrypt` package directly rather than passlib. Passlib is effectively
unmaintained and its bcrypt backend breaks against bcrypt 4.x, so the wrapper
would be the fragile part of the stack rather than the thing it wraps.
"""

import bcrypt

# bcrypt hashes at most 72 BYTES and raises above that rather than truncating.
# Bytes, not characters: an emoji is four bytes, so a 19-character password can
# exceed this. Callers validate against it before hashing so an over-long
# password is a 422 from the API rather than a 500 from here.
MAX_PASSWORD_BYTES = 72


class PasswordTooLongError(ValueError):
    """Raised when a password exceeds what bcrypt can hash."""


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password is {len(encoded)} bytes; bcrypt accepts at most {MAX_PASSWORD_BYTES}."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash.

    Returns False rather than raising for any malformed input. Login must not
    distinguish "wrong password" from "corrupt hash" in its response, and an
    over-long password here is simply a failed attempt, not a server error.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except ValueError:
        return False
