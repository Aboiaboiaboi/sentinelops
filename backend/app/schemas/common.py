from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _to_utc_z(value: datetime) -> str:
    """Render a datetime the way the frontend already produces them.

    Pydantic's default renders UTC as "+00:00". Both parse in JavaScript, but the
    frontend's own fixtures use Date.toISOString(), which emits "Z" — matching it
    means a fixture response and a real response are byte-identical in shape, so
    switching between them can never surface a formatting difference.

    A naive datetime is treated as UTC rather than rejected: every timestamp in
    this schema is stored as timestamptz, so one arriving without an offset means
    a driver dropped it, not that the value is in some other zone.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(_to_utc_z, return_type=str)]
