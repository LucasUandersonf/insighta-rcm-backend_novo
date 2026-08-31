from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class PatientCreateRequest(BaseModel):
    """Sem tenant_id — mesmo motivo de sempre: vem do JWT, nunca do corpo."""

    full_name: str
    cpf: str | None = None
    birth_date: date | None = None
    acquisition_source: str | None = None
    acquisition_campaign_id: str | None = None

    @field_validator("cpf")
    @classmethod
    def sanitize_cpf(cls, v: str | None) -> str | None:
        # Sanitização estrita: remove tudo que não for dígito antes de
        # persistir, para o campo nunca chegar ao banco em formatos
        # inconsistentes ("123.456.789-00" vs "12345678900").
        if v is None:
            return v
        digits = "".join(ch for ch in v if ch.isdigit())
        if digits and len(digits) != 11:
            raise ValueError("CPF deve conter 11 dígitos.")
        return digits or None


class PatientResponse(BaseModel):
    id: UUID
    full_name: str
    cpf: str | None
    birth_date: date | None
    acquisition_source: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
