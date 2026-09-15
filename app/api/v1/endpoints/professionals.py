from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_planned_absence_repository import ProfessionalPlannedAbsenceRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.schemas.professional import (
    PlannedAbsenceCreateRequest,
    PlannedAbsenceResponse,
    ProfessionalCreateRequest,
    ProfessionalResponse,
    ProfessionalUpdateRequest,
)
from app.services.professional_service import ProfessionalService

router = APIRouter(prefix="/professionals", tags=["professionals"])

# Cadastro de profissional (com grade de horários) é decisão operacional/
# administrativa da clínica — mesma tela de Setup mencionada no briefing.
_CAN_WRITE = ("admin", "owner")


def _build_service(db: DbSession) -> ProfessionalService:
    return ProfessionalService(
        ProfessionalRepository(db), ProfessionalAvailabilityRepository(db), ProfessionalPlannedAbsenceRepository(db)
    )


@router.post("", response_model=ProfessionalResponse, status_code=201)
async def create_professional(
    payload: ProfessionalCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> ProfessionalResponse:
    return await _build_service(db).create_professional(current_user.tenant_id, payload)


@router.get("", response_model=list[ProfessionalResponse])
async def list_professionals(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE, "financeiro", "atendimento", "auditor")),
    # False (padrão) é o que alimenta seletores operacionais (ex: campo
    # "Profissional" em Nova Consulta) — nunca deve oferecer alguém
    # desativado para um agendamento novo. True é só para a Tela de
    # Profissionais em si, que precisa mostrar/reativar quem foi
    # desativado.
    include_inactive: bool = Query(False),
) -> list[ProfessionalResponse]:
    return await _build_service(db).list_professionals(include_inactive=include_inactive)


@router.patch("/{professional_id}", response_model=ProfessionalResponse)
async def update_professional(
    professional_id: UUID,
    payload: ProfessionalUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> ProfessionalResponse:
    return await _build_service(db).update_professional(current_user.tenant_id, professional_id, payload)


@router.post("/{professional_id}/planned-absences", response_model=PlannedAbsenceResponse, status_code=201)
async def add_planned_absence(
    professional_id: UUID,
    payload: PlannedAbsenceCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> PlannedAbsenceResponse:
    """"Mapa de Dados Insighta" — Domínio Profissional (Onda 1): lança
    uma ausência futura planejada (férias, licença) — alimenta previsão
    de capacidade futura, não só olhando pra trás."""
    return await _build_service(db).add_planned_absence(current_user.tenant_id, professional_id, payload)


@router.delete("/{professional_id}/planned-absences/{absence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_planned_absence(
    professional_id: UUID,
    absence_id: UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> None:
    await _build_service(db).remove_planned_absence(professional_id, absence_id)
