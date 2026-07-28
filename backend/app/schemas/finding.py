import uuid

from pydantic import BaseModel, ConfigDict

from app.models.finding import Severity


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scan_id: uuid.UUID
    # A plain string rather than a fixed set: the client title-cases anything it
    # does not recognise, so a new scanner category degrades gracefully instead
    # of failing validation here.
    category: str
    severity: Severity
    title: str
    description: str
    recommendation: str
    score_impact: int
