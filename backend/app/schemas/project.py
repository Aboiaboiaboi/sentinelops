import uuid
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import UtcDatetime


class ProjectCreate(BaseModel):
    """Body of POST /projects.

    Note what is not here: framework is detected by a scanner, and user_id comes
    from the auth cookie. Accepting either from the client would let a caller
    assign a project to someone else.
    """

    name: str = Field(min_length=1, max_length=255)
    repository_url: str = Field(min_length=1, max_length=2048)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name cannot be blank.")
        return stripped

    @field_validator("repository_url")
    @classmethod
    def _must_be_http_url(cls, value: str) -> str:
        """Restrict to http(s) with a host.

        A worker will eventually clone this. Without a scheme check, `file:///`
        or an `ssh://` URL reaches the cloner — the first reads local paths, the
        second cannot be handled. Rejecting them at the edge is cheaper than
        teaching every later stage to distrust the value.
        """
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Repository URL must be an http:// or https:// address.")
        return value.strip()


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    repository_url: str
    # Always present, null until a scan detects it. Serialised as an explicit
    # null rather than omitted — the client's type has no optional keys.
    framework: str | None
    created_at: UtcDatetime
