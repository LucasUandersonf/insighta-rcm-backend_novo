from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.local_repository import LocalRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository
from app.schemas.appointment import AppointmentCreateRequest, AppointmentListItem, AppointmentResponse, AppointmentUpdateRequest
from app.schemas.pagination import PaginatedResponse
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/appointments", tags=["appointments"])

_CAN_WRITE = ("atendimento", "admin", "owner")


def _build_service(db: DbSession) -> AppointmentService:
    return AppointmentService(
        AppointmentRepository(db),
        PatientRepository(db),
        ProfessionalRepository(db),
        LocalRepository(db),
        TenantRepository(db),
        webhook_repo=WebhookSubscriptionRepository(db),
    )


@router.post("", response_model=AppointmentResponse, status_code=201)
async def create_appointment(
    payload: AppointmentCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> AppointmentResponse:
    return await _build_service(db).create_appointment(current_user.tenant_id, current_user.id, payload)


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: UUID,
    payload: AppointmentUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> AppointmentResponse:
    """
    Fecha a etapa de Atendimento do fluxo real (Agendamento -> Atendimento
    -> Faturamento) que faltava no produto: marcar falta/cancelamento, ou
    confirmar o atendimento e só então informar procedimento/CID — ver
    DECISÃO completa em AppointmentUpdateRequest (app/schemas/appointment.py).
    """
    return await _build_service(db).update_appointment(appointment_id, payload)


@router.get("/by-patient/{patient_id}", response_model=list[AppointmentResponse])
async def list_appointments_by_patient(
    patient_id: UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE, "financeiro", "auditor")),
) -> list[AppointmentResponse]:
    return await _build_service(db).list_by_patient(patient_id)


@router.get("", response_model=PaginatedResponse[AppointmentListItem])
async def list_appointments(
    date_from: date,
    date_to: date,
    db: DbSession,
    limit: int = 20,
    offset: int = 0,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE, "financeiro", "auditor")),
) -> PaginatedResponse[AppointmentListItem]:
    """
    Peça que faltava depois do Achado 12 da Auditoria de Templates e
    Insights: os insights de canal de agendamento/motivo de
    cancelamento (ver smart_insights_engine.py::_booking_channel_no_show_insight
    / _cancellation_reason_insight) apontavam o problema em AGREGADO,
    mas não existia nenhuma tela que listasse agendamentos individuais
    de um período com esses campos — este endpoint é o destino real do
    botão "Ver agenda" desses dois insights (ver ExecutiveAgendaSummary.tsx,
    frontend).
    """
    return await _build_service(db).list_by_date_range_paginated(date_from, date_to, limit=limit, offset=offset)
