"""ORM models.

Every model is re-exported here so that importing this package registers all of
them on `Base.metadata`. Alembic's autogenerate compares that metadata against
the live database — a model it never imported looks like a table it should drop.
"""

from app.models.finding import Finding, Severity
from app.models.project import Project
from app.models.scan import SCAN_CATEGORIES, CategoryStatus, Scan, ScanStatus
from app.models.user import User

__all__ = [
    "SCAN_CATEGORIES",
    "CategoryStatus",
    "Finding",
    "Project",
    "Scan",
    "ScanStatus",
    "Severity",
    "User",
]
