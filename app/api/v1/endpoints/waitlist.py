"""
app/api/v1/endpoints/waitlist.py

Onda 5 do Plano de Ação, item 16 — ver DECISÃO completa em
app/sql/057_waitlist_entries.sql. Mesmo RBAC de appointments.py: é
rotina de recepção (marcar/resolver fila de espera), não decisão
gerencial.
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.waitlist_entry_repository import WaitlistEntryRepository
from app.schemas.pagination import PaginatedResponse
from app.schemas.waitlist_entry import WaitlistEntryCreateRequest, WaitlistEntryResolveRequest, WaitlistEntryResponse
from app.services.waitlist_entry_service import WaitlistEntryService

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

_CAN_WRITE = ("atendimento", "admin", "owner")
_CAN_READ = (*_CAN_WRITE, "financeiro", "auditor")


def _build_service(db: DbSession) -> WaitlistEntryService:
    return WaitlistEntryService(WaitlistEntryRepository(db), PatientRepository(db), ProfessionalRepository(db))


@router.post("", response_model=WaitlistEntryResponse, status_code=201)
async def create_waitlist_entry(
    payload: WaitlistEntryCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> WaitlistEntryResponse:
    return await _build_service(db).create_waitlist_entry(current_user.tenant_id, uuid.UUID(current_user.id), payload)


@router.get("", response_model=PaginatedResponse[WaitlistEntryResponse])
async def list_waitlist_entries(
    db: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> PaginatedResponse[WaitlistEntryResponse]:
    return await _build_service(db).list_waitlist_entries(status_filter=status_filter, limit=limit, offset=offset)


@router.post("/{waitlist_entry_id}/resolve", response_model=WaitlistEntryResponse)
async def resolve_waitlist_entry(
    waitlist_entry_id: uuid.UUID,
    payload: WaitlistEntryResolveRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> WaitlistEntryResponse:
    """Converte a entrada em consulta real (`appointment_id` já criado
    via POST /appointments) — marca status="agendado"."""
    return await _build_service(db).resolve_waitlist_entry(waitlist_entry_id, payload.appointment_id)


@router.post("/{waitlist_entry_id}/cancel", response_model=WaitlistEntryResponse)
async def cancel_waitlist_entry(
    waitlist_entry_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> WaitlistEntryResponse:
    return await _build_service(db).cancel_waitlist_entry(waitlist_entry_id)
