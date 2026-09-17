from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.core.text_utils import (
    normalize_sex_value,
    sanitize_phone_value,
    validate_cpf_checksum,
    validate_email_value,
)
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


def _sanitize_address_state(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    cleaned = v.strip().upper()
    if len(cleaned) != 2:
        raise ValueError("UF deve conter 2 letras.")
    return cleaned


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
    # Escopo completo de pessoa física (pedido do usuário: "todo sistema
    # tem dados de pessoa física com nome, telefone, data de nascimento,
    # endereço, email, CPF, sexo") — ver DECISÃO completa em
    # app/sql/058_patient_full_identity.sql. Mesmos validadores do
    # caminho de ingestão em massa (app/core/text_utils.py), para o
    # cadastro manual nunca ser menos rigoroso que a importação.
    phone: str | None = None
    email: str | None = None
    sex: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_state: str | None = None

    @field_validator("cpf")
    @classmethod
    def sanitize_cpf(cls, v: str | None) -> str | None:
        # Sanitização estrita: remove tudo que não for dígito e valida o
        # dígito verificador real (mesmo algoritmo da ingestão em massa,
        # ver DECISÃO em validate_cpf_checksum) antes de persistir — CPF
        # é a mesma chave de deduplicação usada por
        # NormalizationService._get_or_create_patient, não faz sentido o
        # cadastro manual aceitar um CPF que a ingestão rejeitaria.
        if v is None:
            return v
        digits = "".join(ch for ch in v if ch.isdigit())
        if not digits:
            return None
        validate_cpf_checksum(digits)
        return digits

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

    @field_validator("phone")
    @classmethod
    def sanitize_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return sanitize_phone_value(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return validate_email_value(v)

    @field_validator("sex")
    @classmethod
    def normalize_sex(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return normalize_sex_value(v)

    @field_validator("address_state")
    @classmethod
    def sanitize_address_state(cls, v: str | None) -> str | None:
        return _sanitize_address_state(v)


class PatientUpdateRequest(BaseModel):
    """PATCH /patients/{id} — campos relacionais que raramente são
    conhecidos no primeiro cadastro (quem indicou, consentimento de
    contato, preferência de horário, CEP, telefone, e-mail...). Todos
    opcionais: só o que for enviado é alterado (ver DECISÃO completa em
    045_patient_relationship_fields.sql e app/sql/058_patient_full_identity.sql).
    `cpf` propositalmente FORA daqui: é a chave de deduplicação de
    paciente (ver NormalizationService._get_or_create_patient) — trocar
    o CPF de um paciente já existente por PATCH arrisca fundir/separar
    identidades silenciosamente, risco que um cadastro novo não tem."""

    referred_by_patient_id: UUID | None = None
    communication_consent: bool | None = None
    preferred_time_window: str | None = None
    zip_code: str | None = None
    birth_date: date | None = None
    phone: str | None = None
    email: str | None = None
    sex: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_state: str | None = None

    @field_validator("zip_code")
    @classmethod
    def sanitize_zip(cls, v: str | None) -> str | None:
        return _sanitize_zip_code(v)

    @field_validator("phone")
    @classmethod
    def sanitize_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return sanitize_phone_value(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return validate_email_value(v)

    @field_validator("sex")
    @classmethod
    def normalize_sex(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return normalize_sex_value(v)

    @field_validator("address_state")
    @classmethod
    def sanitize_address_state(cls, v: str | None) -> str | None:
        return _sanitize_address_state(v)

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
    # Escopo completo de pessoa física (pedido do usuário) — ver DECISÃO
    # completa em app/sql/058_patient_full_identity.sql.
    phone: str | None = None
    email: str | None = None
    sex: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    communication_consent: bool | None = None
    preferred_time_window: str | None = None
    zip_code: str | None = None
    # "Equilíbrio Insighta" (Balanced Scorecard, perna Cliente) — ver
    # DECISÃO completa em patient_value_engine.py. Só GET /patients
    # (PatientService.list_patients_paginated) de fato calcula isso —
    # create/update/anonymize devolvem o default (False/[]) de propósito,
    # nunca um valor calculado só pela metade.
    is_vip: bool = False
    vip_reasons: list[str] = []

    model_config = {"from_attributes": True}


class PatientBirthdayItem(BaseModel):
    """Achado do Dossiê Insighta RCM — Patient.birth_date já era
    capturado, mas nenhuma tela listava aniversariantes do mês.
    `communication_consent` vai junto de propósito: é o sinal que já
    existe sobre se a clínica tem aval do paciente pra entrar em
    contato (ex: ligar/mandar mensagem de parabéns) — a lista nunca
    filtra por ele (não é um envio automático, é uma lista de apoio
    pra decisão humana), só expõe pra quem for usar a lista saber."""

    patient_id: UUID
    full_name: str
    birth_date: date
    communication_consent: bool | None


class PatientBirthdaysResponse(BaseModel):
    """GET /api/v1/patients/birthdays — ordenado por dia do mês."""

    month: int
    items: list[PatientBirthdayItem]
