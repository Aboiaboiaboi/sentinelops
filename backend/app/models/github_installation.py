import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class GitHubInstallation(Base):
    """One installation of our GitHub App on someone's account.

    Deliberately *not* a credential store. The row records only which
    installation belongs to which user; tokens are minted on demand from the
    installation id and never persisted — that is the entire reason a GitHub
    App was chosen over stored personal access tokens.
    """

    __tablename__ = "github_installations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Indexed because every query is scoped to the authenticated user, same as
    # projects.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # GitHub's identifier. BigInteger because GitHub ids are int64 on the
    # wire, and unique because one installation cannot belong to two users —
    # a re-run of the setup flow re-points the existing row instead.
    installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

    # The GitHub account (user or organisation) the App is installed on. For
    # display only; never used for authorisation.
    account_login: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="github_installations", lazy="raise_on_sql"
    )
