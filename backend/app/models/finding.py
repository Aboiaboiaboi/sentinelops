import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Severity(enum.StrEnum):
    """Finding severity.

    Uppercase on the wire — the only enum in the contract that is. The frontend
    uses these strings directly as lookup keys for badge styling, so the casing
    is load-bearing rather than cosmetic.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_severity_enum = Enum(
    Severity,
    name="severity",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )

    # A plain string, not the scanner-category enum. Scanners are a pluggable
    # boundary, and a new category should not require a migration to record.
    category: Mapped[str] = mapped_column(String(50), index=True)

    severity: Mapped[Severity] = mapped_column(_severity_enum)

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)

    # Points this single finding deducted. Stored per finding so a score can be
    # explained line by line instead of presented as an opaque total.
    score_impact: Mapped[int] = mapped_column(Integer)

    scan: Mapped["Scan"] = relationship(  # noqa: F821
        back_populates="findings", lazy="raise_on_sql"
    )
