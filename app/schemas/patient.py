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
    # "Equilíbrio Insighta" (Balanced Scorecard, perna Cliente) — ver
    # DECISÃO completa em patient_value_engine.py. Só GET /patients
    # (PatientService.list_patients_paginated) de fato calcula isso —
    # create/update/anonymize devolvem o default (False/[]) de propósito,
    # nunca um valor calculado só pela metade.
    is_vip: bool = False
    vip_reasons: list[str] = []

    model_config = {"from_attributes": True}


class PatientSearchItem(BaseModel):
    """Busca por nome/CPF (GET /patients/search) — mesmo padrão enxuto de
    BillingSearchItem, alimenta o autocomplete da Ficha do Paciente."""

    id: UUID
    full_name: str
    cpf: str | None


class PatientFichaBillingItem(BaseModel):
    """Ficha do Paciente (Roadmap "Rumo à Nota 9", Fase 4) — o registro de
    faturamento vinculado a UM atendimento específico do paciente. Pode
    haver mais de um billing por atendimento (SADT com vários
    procedimentos), ou zero (atendimento ainda não faturado)."""

    id: UUID
    charged_value: float
    status: str
    denial_risk_level: str
    created_at: datetime


class PatientFichaAppointmentItem(BaseModel):
    """Um atendimento do paciente, com os faturamentos gerados a partir
    dele já aninhados — pedido direto do usuário: "cada conta possui um
    registro depois da abertura de atendimento, não seria legal termos
    isto". `professional_name`/`insurance_plan_name` None quando o
    agendamento não tem esses vínculos preenchidos."""

    id: UUID
    scheduled_at: datetime
    status: str
    professional_name: str | None
    insurance_plan_name: str | None
    no_show_risk_level: str | None
    billings: list[PatientFichaBillingItem]


class PatientFichaSummary(BaseModel):
    """Números agregados de todo o histórico do paciente (não escopado
    por período — é a ficha da PESSOA, não de uma janela de tempo)."""

    total_appointments: int
    no_show_count: int
    no_show_rate: float | None  # None quando não há amostra (0 atendimentos resolvidos)
    total_billed: float
    total_value_saved: float
    last_visit_at: datetime | None


class PatientFichaResponse(BaseModel):
    """GET /patients/{patient_id}/ficha — junta pessoa física + histórico
    de agendamento + histórico de atendimento/faturamento numa única
    visão, cruzando os três dados que hoje só existiam separados em
    telas diferentes (achado direto do usuário)."""

    patient: PatientResponse
    summary: PatientFichaSummary
    # Mais recente primeiro, capado (ver DECISÃO em
    # PatientRepository.get_ficha_appointments) — ficha não é uma tela de
    # auditoria de todo o histórico, é um resumo pra decisão rápida.
    appointments: list[PatientFichaAppointmentItem]
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
