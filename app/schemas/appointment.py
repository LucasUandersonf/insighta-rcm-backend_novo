from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Vocabulário FECHADO — os únicos 4 valores que no_show_risk_engine.py,
# capacity_repository.py e analytics_repository.py já sabem interpretar
# (ver grep de literais "scheduled"/"completed"/"no_show"/"cancelled" no
# código). Um status fora deste conjunto não dá erro nenhum — some
# silenciosamente de toda métrica que filtra por status conhecido (SQL
# comparando string, não um enum de banco) — por isso a validação aqui,
# não porque o domínio de negócio real só tenha 4 estados possíveis
# (ver DECISÃO em AppointmentUpdateRequest sobre estados intermediários
# como "confirmado").
_KNOWN_STATUSES = ("scheduled", "completed", "no_show", "cancelled")


class AppointmentCreateRequest(BaseModel):
    patient_id: UUID
    insurance_plan_id: UUID | None = None
    professional_id: UUID | None = None
    scheduled_at: datetime
    duration_minutes: int | None = Field(default=None, gt=0)
    procedure_code: str | None = None
    cid_code: str | None = None


class AppointmentUpdateRequest(BaseModel):
    """
    PATCH parcial — só aplica campos explicitamente enviados (mesmo
    padrão de ProfessionalUpdateRequest/InsuranceCompanyUpdateRequest).

    BUG CORRIGIDO (achado analisando o fluxo real Agendamento ->
    Atendimento -> Faturamento do mercado, ver conversa sobre ERP
    Moderna) — antes deste schema/endpoint não existia NENHUM jeito de
    transicionar o status de um agendamento criado via `POST
    /appointments`: ele nascia "scheduled" e ficava assim para sempre
    (só a ingestão em massa de CSV gravava "completed" direto, via
    normalization_service.py). Não havia como a recepção marcar "faltou"
    nem o profissional confirmar "atendido" depois da consulta — a Etapa
    de Atendimento do fluxo real não tinha ação nenhuma no produto para
    quem NÃO usa importação de arquivo.

    `procedure_code`/`cid_code` também viram editáveis aqui de propósito:
    no fluxo real, o procedimento e o CID muitas vezes só são conhecidos
    DEPOIS da consulta (o paciente agenda "consulta", não um código TUSS
    específico) — exigi-los só na criação (Agendamento) forçaria o
    usuário a adivinhar na hora de marcar o horário.

    DECISÃO — só os 4 status já entendidos pelo resto do sistema
    -------------------------------------------------------------
    Um estado intermediário como "confirmado" (comum em ERPs reais,
    normalmente após lembrete por WhatsApp) fica de fora por ora: hoje
    não há canal de confirmação de paciente no produto (o WhatsApp
    client existente é só para distribuição de relatório aos sócios,
    não lembrete de consulta) e adicionar um 5º estado exigiria decidir
    como cada agregação de capacidade/ocupação/no-show deve tratá-lo —
    decisão que depende do formato real do ERP de origem (ver
    conversa/kit de teste em andamento), não deveria ser adivinhada
    aqui.
    """

    status: str | None = None
    procedure_code: str | None = None
    cid_code: str | None = None
    duration_minutes: int | None = Field(default=None, gt=0)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _KNOWN_STATUSES:
            raise ValueError(f"status deve ser um de: {', '.join(_KNOWN_STATUSES)}")
        return v


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
