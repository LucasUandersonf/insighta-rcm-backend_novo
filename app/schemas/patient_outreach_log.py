"""
app/schemas/patient_outreach_log.py

Onda 4 do Plano de Ação, item 12 — ver DECISÃO completa em
app/sql/055_patient_outreach_log.sql.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PatientOutreachLogCreateRequest(BaseModel):
    channel: str = Field(pattern="^(telefone|whatsapp|sms|email|presencial)$")
    outcome: str = Field(pattern="^(contatado|sem_resposta|agendou|recusou)$")
    notes: str | None = None


class PatientOutreachLogResponse(BaseModel):
    id: UUID
    patient_id: UUID
    channel: str
    outcome: str
    notes: str | None
    created_by: UUID
    created_at: datetime

    model_config = {"from_attributes": True}
