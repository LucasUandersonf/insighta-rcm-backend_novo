from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, model_validator


class AvailabilityBlockRequest(BaseModel):
    weekday: int  # 0=domingo .. 6=sábado
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def check_order(self) -> "AvailabilityBlockRequest":
        if not (0 <= self.weekday <= 6):
            raise ValueError("weekday deve estar entre 0 (domingo) e 6 (sábado).")
        if self.end_time <= self.start_time:
            raise ValueError("end_time deve ser posterior a start_time.")
        return self


class ProfessionalCreateRequest(BaseModel):
    full_name: str
    professional_registry: str | None = None
    specialty: str | None = None
    # Grade semanal já cadastrada de uma vez, junto com o profissional —
    # evita duas chamadas de API para o caso comum (cadastrar profissional
    # já com seus horários fixos).
    availability: list[AvailabilityBlockRequest] = []


class AvailabilityBlockResponse(BaseModel):
    weekday: int
    start_time: time
    end_time: time

    model_config = {"from_attributes": True}


class ProfessionalUpdateRequest(BaseModel):
    """PATCH /professionals/{id} — todos os campos opcionais (payload
    parcial, mesmo contrato de UserUpdateRequest). `availability`,
    quando informado, SUBSTITUI a grade inteira (mesma semântica de
    HomologateRequest.items em contracts.py: a Tela de Profissionais
    sempre manda a lista completa e final da grade revisada, não um
    diff incremental) — None mantém a grade atual intacta, [] some
    com ela por completo (profissional passa a não ter capacidade
    teórica instalada nenhuma)."""

    full_name: str | None = None
    professional_registry: str | None = None
    specialty: str | None = None
    is_active: bool | None = None
    availability: list[AvailabilityBlockRequest] | None = None


class PlannedAbsenceCreateRequest(BaseModel):
    """"Mapa de Dados Insighta" — Domínio Profissional (Onda 1): férias/
    licença FUTURA planejada — diferente da grade semanal (que é
    recorrente), aqui é um intervalo de datas específico."""

    start_date: date
    end_date: date
    reason: str | None = None

    @model_validator(mode="after")
    def check_order(self) -> "PlannedAbsenceCreateRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date deve ser igual ou posterior a start_date.")
        return self


class PlannedAbsenceResponse(BaseModel):
    id: UUID
    start_date: date
    end_date: date
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfessionalResponse(BaseModel):
    id: UUID
    full_name: str
    professional_registry: str | None
    specialty: str | None
    is_active: bool
    availability: list[AvailabilityBlockResponse] = []
    # "Mapa de Dados Insighta" — Domínio Profissional (Onda 1).
    planned_absences: list[PlannedAbsenceResponse] = []

    model_config = {"from_attributes": True}
