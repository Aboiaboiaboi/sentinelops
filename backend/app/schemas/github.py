import uuid

from pydantic import BaseModel, ConfigDict

from app.schemas.common import UtcDatetime


class GitHubInstallationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    installation_id: int
    account_login: str
    created_at: UtcDatetime


class GitHubRepositoryRead(BaseModel):
    """One repository the App can see, shaped for the picker.

    Not persisted anywhere — assembled from GitHub's response on each request,
    so the picker always reflects what the user currently grants.
    """

    full_name: str
    private: bool
    # The https clone URL, which is what a created project stores.
    url: str
    # Which installation grants access, so a scan of a private repository
    # knows where to mint its token from.
    installation_id: int
