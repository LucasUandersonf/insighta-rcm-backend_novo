from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.patient import PREFERRED_TIME_WINDOW_VALUES


def _sanitize_zip_code(v: str | None) -> str | None:
    # Mesmo espírito de sanitize_cpf: só dígitos, nunca formatado
    # ("01310-100" -> "01310100"), para nunca chegar ao banco em
    # formatos inconsistentes.
    if v is None:
        return v
    digits = "".join(ch for ch in v if ch.isdigit())
    if digits and len(digits) != 8:
        raise ValueError("CEP deve conter 8 dígitos.")
    return digits or None


class PatientCreateRequest(BaseModel):
    """Sem tenant_id — mesmo motivo de sempre: vem do JWT, nunca do corpo."""

    full_name: str
    cpf: str | None = None
    birth_date: date | None = None
    acquisition_source: str | None = None
    acquisition_campaign_id: str | None = None
    # "Mapa de Dados Insighta" — Domínio Paciente (Onda 1). Ver DECISÃO
    # completa em 045_patient_relationship_fields.sql.
    referred_by_patient_id: UUID | None = None
    communication_consent: bool | None = None
    preferred_time_window: str | None = None
    zip_code: str | None = None

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

    @field_validator("zip_code")
    @classmethod
    def sanitize_zip(cls, v: str | None) -> str | None:
        return _sanitize_zip_code(v)

    @field_validator("preferred_time_window")
    @classmethod
    def validate_preferred_time_window(cls, v: str | None) -> str | None:
        if v is not None and v not in PREFERRED_TIME_WINDOW_VALUES:
            raise ValueError(f"preferred_time_window deve ser um de: {', '.join(PREFERRED_TIME_WINDOW_VALUES)}.")
        return v


class PatientUpdateRequest(BaseModel):
    """PATCH /patients/{id} — campos relacionais que raramente são
    conhecidos no primeiro cadastro (quem indicou, consentimento de
    contato, preferência de horário, CEP). Todos opcionais: só o que
    for enviado é alterado (ver DECISÃO completa em
    045_patient_relationship_fields.sql)."""

    referred_by_patient_id: UUID | None = None
    communication_consent: bool | None = None
    preferred_time_window: str | None = None
    zip_code: str | None = None

    @field_validator("zip_code")
    @classmethod
    def sanitize_zip(cls, v: str | None) -> str | None:
        return _sanitize_zip_code(v)

    @field_validator("preferred_time_window")
    @classmethod
    def validate_preferred_time_window(cls, v: str | None) -> str | None:
        if v is not None and v not in PREFERRED_TIME_WINDOW_VALUES:
            raise ValueError(f"preferred_time_window deve ser um de: {', '.join(PREFERRED_TIME_WINDOW_VALUES)}.")
        return v


class PatientResponse(BaseModel):
    id: UUID
    full_name: str
    cpf: str | None
    birth_date: date | None
    acquisition_source: str | None
    created_at: datetime
    # Ver DECISÃO em app/sql/022_patient_lgpd_erasure.sql — None = dado
    # pessoal intacto; preenchido = paciente já anonimizado a pedido do
    # titular (LGPD art. 18, VI).
    anonymized_at: datetime | None = None
    # "Mapa de Dados Insighta" — Domínio Paciente (Onda 1).
    referred_by_patient_id: UUID | None = None
    communication_consent: bool | None = None
    preferred_time_window: str | None = None
    zip_code: str | None = None

    model_config = {"from_attributes": True}
