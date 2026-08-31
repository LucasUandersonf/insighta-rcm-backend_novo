from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AppointmentCreateRequest(BaseModel):
    patient_id: UUID
    insurance_plan_id: UUID | None = None
    professional_id: UUID | None = None
    scheduled_at: datetime
    duration_minutes: int | None = Field(default=None, gt=0)
    procedure_code: str | None = None
    cid_code: str | None = None


class AppointmentResponse(BaseModel):
    id: UUID
    patient_id: UUID
    insurance_plan_id: UUID | None
    professional_id: UUID | None
    scheduled_at: datetime
    duration_minutes: int | None
    status: str
    procedure_code: str | None
    cid_code: str | None
    no_show_risk_level: str | None
    no_show_risk_score: float | None
    created_at: datetime

    model_config = {"from_attributes": True}
