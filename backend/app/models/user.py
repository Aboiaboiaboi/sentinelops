import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # 320 is the maximum length RFC 5321 permits. Unique + indexed because login
    # looks a user up by email on every authentication.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)

    # Never leaves this layer. The response schemas in schemas/user.py exist
    # precisely so this column has no route out to the API.
    password_hash: Mapped[str] = mapped_column(String(255))

    # Python-side default so the value exists on the instance immediately after
    # commit. With expire_on_commit=False the ORM does not re-read the row, so a
    # server_default alone would leave this None on the object a route returns.
    # The server_default is kept as a backstop for rows inserted outside the ORM.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    projects: Mapped[list["Project"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        # The database enforces the cascade via ON DELETE CASCADE, so the ORM
        # does not need to load every child row just to delete it.
        passive_deletes=True,
        # Async guard: a lazy load triggered outside an await raises
        # MissingGreenlet, which surfaces far from its cause. This turns it into
        # an explicit error naming the relationship instead.
        lazy="raise_on_sql",
    )
