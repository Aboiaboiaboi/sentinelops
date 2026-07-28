import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class ScanStatus(enum.StrEnum):
    """Lifecycle of a scan. The frontend polls until this reaches a terminal
    value, treating only `completed` and `failed` as terminal."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CategoryStatus(enum.StrEnum):
    """Per-category outcome inside `category_status`.

    Not a database enum — it describes the values of a JSONB map, not a column.
    `failed` and `pending` are genuinely different: each category runs in its own
    sandbox, so one timing out leaves the scan completable with partial results.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"


# The categories a scan reports on. Each runs independently, so a scan carries a
# status per category rather than one overall outcome.
#
# Not a database enum: these are the keys of a JSONB map, and adding a category
# should not need a migration.
SCAN_CATEGORIES: tuple[str, ...] = (
    "architecture",
    "security",
    "deployment",
    "reliability",
    "observability",
    "scalability",
)


# SQLAlchemy stores an Enum column by member NAME by default, which would write
# "PENDING" where the API contract requires "pending". values_callable forces it
# to persist the value instead, so what is in the column is what goes on the wire.
_scan_status_enum = Enum(
    ScanStatus,
    name="scan_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[ScanStatus] = mapped_column(_scan_status_enum, default=ScanStatus.PENDING)

    # Null until the scan completes — the API returns literal null, and the UI
    # renders a placeholder rather than a zero.
    score: Mapped[int | None] = mapped_column(Integer, default=None)

    # Which weight-set produced `score`. Without it an old score is not
    # interpretable after the weights change.
    scoring_version: Mapped[str | None] = mapped_column(String(20), default=None)

    # Maps category name -> CategoryStatus value. JSONB rather than JSON so the
    # contents stay queryable if scores ever need filtering by category outcome.
    category_status: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    project: Mapped["Project"] = relationship(  # noqa: F821
        back_populates="scans", lazy="raise_on_sql"
    )
    findings: Mapped[list["Finding"]] = relationship(  # noqa: F821
        back_populates="scan",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
