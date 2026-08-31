from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.schemas.professional import ProfessionalCreateRequest, ProfessionalResponse
from app.services.professional_service import ProfessionalService

router = APIRouter(prefix="/professionals", tags=["professionals"])

# Cadastro de profissional (com grade de horários) é decisão operacional/
# administrativa da clínica — mesma tela de Setup mencionada no briefing.
_CAN_WRITE = ("admin", "owner")


@router.post("", response_model=ProfessionalResponse, status_code=201)
async def create_professional(
    payload: ProfessionalCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> ProfessionalResponse:
    service = ProfessionalService(ProfessionalRepository(db), ProfessionalAvailabilityRepository(db))
    return await service.create_professional(current_user.tenant_id, payload)


@router.get("", response_model=list[ProfessionalResponse])
async def list_professionals(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE, "financeiro", "atendimento", "auditor")),
) -> list[ProfessionalResponse]:
    service = ProfessionalService(ProfessionalRepository(db), ProfessionalAvailabilityRepository(db))
    return await service.list_professionals()
