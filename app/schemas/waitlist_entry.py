"""
app/schemas/waitlist_entry.py

Onda 5 do Plano de Ação, item 16 — ver DECISÃO completa em
app/sql/057_waitlist_entries.sql.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.patient import PREFERRED_TIME_WINDOW_VALUES
from app.models.waitlist_entry import WAITLIST_STATUS_VALUES


class WaitlistEntryCreateRequest(BaseModel):
    patient_id: UUID
    professional_id: UUID | None = None
    procedure_code: str | None = None
    preferred_time_window: str | None = Field(default=None, pattern="^(" + "|".join(PREFERRED_TIME_WINDOW_VALUES) + ")$")
    notes: str | None = None


class WaitlistEntryResolveRequest(BaseModel):
    appointment_id: UUID


class WaitlistEntryResponse(BaseModel):
    id: UUID
    patient_id: UUID
    patient_full_name: str
    professional_id: UUID | None
    professional_full_name: str | None
    procedure_code: str | None
    preferred_time_window: str | None
    notes: str | None
    status: str = Field(pattern="^(" + "|".join(WAITLIST_STATUS_VALUES) + ")$")
    resolved_appointment_id: UUID | None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
