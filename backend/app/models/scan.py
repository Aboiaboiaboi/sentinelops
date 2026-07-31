import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Uuid, func, text
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


class ScanErrorCategory(enum.StrEnum):
    """Why a scan failed, in terms a user can act on.

    Stored in a plain String column rather than a Postgres enum, for the same
    reason SCAN_CATEGORIES is not one: this list will grow (Phase 3's sandbox
    failures are not here yet) and adding a value should not need a migration.
    It also sidesteps the enum-drop trap — Alembic never generates the DROP TYPE
    for a Postgres enum, so a downgrade leaves an orphan behind that makes the
    next upgrade fail.
    """

    REPOSITORY_NOT_FOUND = "repository_not_found"
    AUTHENTICATION = "authentication"
    NETWORK_UNREACHABLE = "network_unreachable"
    REPOSITORY_TOO_LARGE = "repository_too_large"
    TIMEOUT = "timeout"
    CLONE_FAILED = "clone_failed"
    INTERNAL = "internal"


# What to tell someone for each cause. Kept beside the enum so a new category
# cannot be added without deciding what advice goes with it.
SCAN_ERROR_HINTS: dict[str, str] = {
    ScanErrorCategory.REPOSITORY_NOT_FOUND: (
        "Check the URL for typos. If the repository is private, connect GitHub and grant "
        "access to it — a private repository is indistinguishable from a missing one until "
        "you do."
    ),
    ScanErrorCategory.AUTHENTICATION: (
        "The repository exists but access was refused. Connect GitHub from the dashboard, "
        "or use Manage access to include this repository in what you have granted."
    ),
    ScanErrorCategory.NETWORK_UNREACHABLE: (
        "The host could not be reached. This is usually temporary — try the scan again in "
        "a few minutes."
    ),
    ScanErrorCategory.REPOSITORY_TOO_LARGE: (
        "The repository exceeds the size limits a scan will accept. Scanning a smaller "
        "repository, or one without large committed binaries, will work."
    ),
    ScanErrorCategory.TIMEOUT: (
        "Cloning took longer than the time limit. A large repository or a slow network can "
        "cause this; trying again often succeeds."
    ),
    ScanErrorCategory.CLONE_FAILED: (
        "The repository could not be cloned. Check that the URL points at a git repository "
        "that exists and is reachable."
    ),
    ScanErrorCategory.INTERNAL: (
        "Something went wrong on our side rather than with your repository. Running the "
        "scan again is worth trying; if it keeps failing, the repository may be hitting an "
        "edge case worth reporting."
    ),
}


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

    # Points each completed category actually earned, so a client can show what
    # a category scored rather than assuming it scored full marks.
    #
    # Stored rather than derived on read. Deriving would mean loading every
    # finding on GET /scans/{id}, which the client polls every three seconds —
    # the polled endpoint has to stay a single indexed row read.
    category_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    # Why a failed scan failed. Null for anything that has not failed.
    #
    # `error_detail` is built from the *exception type*, never from git's
    # stderr: stderr can echo the clone URL, and for a private repository that
    # URL carries an installation token. stderr is read to classify the failure
    # and then discarded — it reaches the worker log and nothing else.
    error_category: Mapped[str | None] = mapped_column(String(40), default=None)
    error_detail: Mapped[str | None] = mapped_column(String(500), default=None)

    # The HEAD commit the scan actually looked at. All nullable together: a
    # repository with no commits has no HEAD, and commit context is an extra on
    # top of a scan rather than a precondition for one — a scan that could not
    # read it still completed and still has a score.
    #
    # Recorded per scan rather than per project because the whole point is that
    # it changes between scans; it is what makes a score change attributable to
    # a change in the code.
    commit_sha: Mapped[str | None] = mapped_column(String(40), default=None)
    commit_message: Mapped[str | None] = mapped_column(String(500), default=None)
    commit_author: Mapped[str | None] = mapped_column(String(255), default=None)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

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
