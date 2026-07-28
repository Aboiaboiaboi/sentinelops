import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.auth.security import MAX_PASSWORD_BYTES
from app.schemas.common import UtcDatetime

MIN_PASSWORD_LENGTH = 8


class Credentials(BaseModel):
    """Body of POST /auth/signup and POST /auth/login."""

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        """Lower-case the address so signup and login agree on identity.

        RFC 5321 permits a case-sensitive local part, but no mail provider in
        practice treats one that way. Storing it verbatim would let the same
        person register twice and then fail to log in with the casing they typed.
        """
        return value.lower()

    @field_validator("password")
    @classmethod
    def _fits_bcrypt(cls, value: str) -> str:
        """Reject what bcrypt cannot hash, in bytes rather than characters.

        Field(max_length=...) counts characters, so it would pass a 20-character
        emoji password that is 80 bytes and blows up inside bcrypt. Checking here
        makes an over-long password a 422 with a readable message instead of a
        500 from the hashing layer.
        """
        encoded = len(value.encode("utf-8"))
        if encoded > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"Password must be at most {MAX_PASSWORD_BYTES} bytes; this one is {encoded}. "
                "Accented and emoji characters count as several bytes each."
            )
        return value


class UserRead(BaseModel):
    """What the API returns for a user.

    Note what is absent: password_hash exists on the model and has no route out
    through here. That separation is the entire reason schemas/ is not models/.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: UtcDatetime
