import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

# Defined in the scanner boundary and re-exported here, so that importing a
# scanner does not pull SQLAlchemy in with it. Severity is a property of what a
# scanner produces; the database is one of the places it ends up.
from app.scanners.base import Severity

__all__ = ["Finding", "Severity"]


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
