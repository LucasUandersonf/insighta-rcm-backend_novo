"""
app/schemas/local.py

Local de Atendimento (Unidade/Setor) — ver DECISÃO completa em
app/models/local.py e app/sql/018_locais_tipo_paciente.sql.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LocalCreateRequest(BaseModel):
    nome: str = Field(min_length=1, max_length=255)


class LocalUpdateRequest(BaseModel):
    """PATCH parcial — só aplica campos explicitamente enviados (mesmo
    padrão de ProfessionalUpdateRequest/InsuranceCompanyUpdateRequest).
    `is_active=false` é a forma de "excluir" um local cadastrado errado/
    duplicado sem quebrar as FKs de Appointment."""

    nome: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class LocalResponse(BaseModel):
    id: UUID
    nome: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
