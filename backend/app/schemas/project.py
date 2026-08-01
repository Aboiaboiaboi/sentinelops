import uuid
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import UtcDatetime


def _strip_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("Name cannot be blank.")
    return stripped


def _must_be_http_url(value: str) -> str:
    """Restrict to http(s) with a host.

    A worker will eventually clone this. Without a scheme check, `file:///`
    or an `ssh://` URL reaches the cloner — the first reads local paths, the
    second cannot be handled. Rejecting them at the edge is cheaper than
    teaching every later stage to distrust the value.

    Module-level so create and update enforce exactly the same rule. An edit
    that accepted a URL creation rejects would be a way in through the back
    door.
    """
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Repository URL must be an http:// or https:// address.")
    return value.strip()


class ProjectCreate(BaseModel):
    """Body of POST /projects.

    Note what is not here: framework is detected by a scanner, and user_id comes
    from the auth cookie. Accepting either from the client would let a caller
    assign a project to someone else.
    """

    name: str = Field(min_length=1, max_length=255)
    repository_url: str = Field(min_length=1, max_length=2048)

    _strip = field_validator("name")(_strip_name)
    _url = field_validator("repository_url")(_must_be_http_url)


class ProjectUpdate(BaseModel):
    """Body of PATCH /projects/{id}.

    Every field optional, and absence means "leave it alone" rather than
    "clear it" — the service reads only the fields that were actually sent, so
    updating a name never touches the URL.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    repository_url: str | None = Field(default=None, min_length=1, max_length=2048)

    _strip = field_validator("name")(_strip_name)
    _url = field_validator("repository_url")(_must_be_http_url)


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
    # Whether the URL can still be changed. Sent so the client can disable the
    # field with an explanation rather than letting somebody type a new URL and
    # discover on save that it was never allowed.
    repository_url_editable: bool
