import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.auth.jwt import create_access_token, decode_access_token
from app.auth.security import (
    MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    hash_password,
    verify_password,
)
from app.config import DEV_SECRET_KEY, MIN_SECRET_KEY_BYTES, Settings, get_settings


class TestPasswordHashing:
    async def test_correct_password_verifies(self) -> None:
        stored = await hash_password("correct horse battery")

        assert await verify_password("correct horse battery", stored)

    async def test_wrong_password_does_not_verify(self) -> None:
        stored = await hash_password("correct horse battery")

        assert not await verify_password("wrong", stored)

    async def test_hash_is_not_the_password(self) -> None:
        password = "correct horse battery"

        assert await hash_password(password) != password

    async def test_same_password_hashes_differently_each_time(self) -> None:
        """Distinct salts. Identical hashes would reveal which users share a
        password just by reading the table."""
        password = "correct horse battery"

        assert await hash_password(password) != await hash_password(password)

    async def test_password_at_the_byte_limit_is_accepted(self) -> None:
        password = "a" * MAX_PASSWORD_BYTES

        assert await verify_password(password, await hash_password(password))

    async def test_password_over_the_byte_limit_is_rejected(self) -> None:
        with pytest.raises(PasswordTooLongError):
            await hash_password("a" * (MAX_PASSWORD_BYTES + 1))

    async def test_limit_counts_bytes_not_characters(self) -> None:
        """An emoji is four bytes, so this is 20 characters but 80 bytes. Counting
        characters here would let it through and crash inside bcrypt instead."""
        password = "😀" * 20

        assert len(password) < MAX_PASSWORD_BYTES
        with pytest.raises(PasswordTooLongError):
            await hash_password(password)

    async def test_verifying_an_over_long_password_is_false_not_an_error(self) -> None:
        assert not await verify_password("a" * 200, await hash_password("short"))

    async def test_verifying_against_a_corrupt_hash_is_false_not_an_error(self) -> None:
        assert not await verify_password("anything", "not-a-bcrypt-hash")

    async def test_hashing_does_not_block_the_event_loop(self) -> None:
        """The reason these are async at all.

        bcrypt costs ~200ms of CPU. Called directly from a coroutine it stalls
        every other request the worker is serving, not just its own — measured
        at 3ms to 1561ms for an unrelated endpoint under ten concurrent logins.
        Running it in a thread means the loop stays free to make progress.
        """
        ticks = 0

        async def count_ticks() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        ticker = asyncio.create_task(count_ticks())
        try:
            await hash_password("correct horse battery")
        finally:
            ticker.cancel()

        # A blocked loop would leave this at zero: the ticker never gets to run.
        assert ticks > 0


class TestAccessTokens:
    def test_round_trips_the_user_id(self) -> None:
        user_id = uuid.uuid4()

        assert decode_access_token(create_access_token(user_id)) == user_id

    def test_rejects_a_tampered_token(self) -> None:
        token = create_access_token(uuid.uuid4())
        tampered = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")

        assert decode_access_token(tampered) is None

    def test_rejects_a_token_signed_with_another_key(self) -> None:
        settings = get_settings()
        foreign = jwt.encode(
            {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(hours=1)},
            # At least 32 bytes, so this exercises a valid-but-wrong key rather
            # than tripping PyJWT's short-key warning.
            "a-different-secret-of-sufficient-length",
            algorithm=settings.jwt_algorithm,
        )

        assert decode_access_token(foreign) is None

    def test_rejects_an_expired_token(self) -> None:
        settings = get_settings()
        expired = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "iat": datetime.now(UTC) - timedelta(hours=2),
                "exp": datetime.now(UTC) - timedelta(hours=1),
            },
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )

        assert decode_access_token(expired) is None

    def test_rejects_an_unsigned_token(self) -> None:
        """The `alg: none` attack. A token asking to be trusted without a
        signature must not be, which is what pinning `algorithms` prevents."""
        unsigned = jwt.encode(
            {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(hours=1)},
            key="",
            algorithm="none",
        )

        assert decode_access_token(unsigned) is None

    def test_rejects_a_token_whose_subject_is_not_a_uuid(self) -> None:
        settings = get_settings()
        token = jwt.encode(
            {"sub": "not-a-uuid", "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )

        assert decode_access_token(token) is None

    def test_rejects_a_token_with_no_subject(self) -> None:
        settings = get_settings()
        token = jwt.encode(
            {"exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )

        assert decode_access_token(token) is None

    def test_rejects_garbage(self) -> None:
        assert decode_access_token("not.a.token") is None


class TestProductionSecretValidation:
    """The default secret is fine in development and must be fatal in production.

    Checked at startup rather than at first login, so a misconfigured deployment
    fails to boot instead of quietly issuing forgeable tokens.
    """

    def test_development_accepts_the_default_secret(self) -> None:
        assert Settings(environment="development").secret_key == DEV_SECRET_KEY

    def test_production_rejects_the_default_secret(self) -> None:
        with pytest.raises(ValidationError, match="SECRET_KEY must be set explicitly"):
            Settings(environment="production", secret_key=DEV_SECRET_KEY)

    def test_production_rejects_a_short_secret(self) -> None:
        with pytest.raises(ValidationError, match="at least 32 bytes"):
            Settings(environment="production", secret_key="a" * (MIN_SECRET_KEY_BYTES - 1))

    def test_production_accepts_a_long_enough_secret(self) -> None:
        secret = "b" * MIN_SECRET_KEY_BYTES

        assert Settings(environment="production", secret_key=secret).secret_key == secret
