from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.schemas.appointment import AppointmentCreateRequest, AppointmentResponse
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/appointments", tags=["appointments"])

_CAN_WRITE = ("atendimento", "admin", "owner")


@router.post("", response_model=AppointmentResponse, status_code=201)
async def create_appointment(
    payload: AppointmentCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> AppointmentResponse:
    service = AppointmentService(AppointmentRepository(db), PatientRepository(db), ProfessionalRepository(db))
    return await service.create_appointment(current_user.tenant_id, current_user.id, payload)


@router.get("/by-patient/{patient_id}", response_model=list[AppointmentResponse])
async def list_appointments_by_patient(
    patient_id: UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE, "financeiro", "auditor")),
) -> list[AppointmentResponse]:
    service = AppointmentService(AppointmentRepository(db), PatientRepository(db), ProfessionalRepository(db))
    return await service.list_by_patient(patient_id)
