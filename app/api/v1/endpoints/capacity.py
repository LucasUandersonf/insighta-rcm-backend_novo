"""
app/api/v1/endpoints/capacity.py

Analytics de agenda — Capacity & Utilization Management. Endpoint
estratégico (percentual de uso da capacidade instalada, no-show), então
RBAC fica igual ao de `contracts`: financeiro/admin/owner, não
'atendimento' (que lida com a agenda operacional do dia, não com o
indicador agregado).
"""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.schemas.capacity import UtilizationResponse
from app.services.capacity_service import CapacityService

router = APIRouter(prefix="/capacity", tags=["capacity"])

_CAN_READ = ("financeiro", "admin", "owner")


@router.get("/utilization/{professional_id}", response_model=UtilizationResponse)
async def get_professional_utilization(
    professional_id: UUID,
    db: DbSession,
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> UtilizationResponse:
    if date_to < date_from:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date_to deve ser >= date_from.")

    professional_repo = ProfessionalRepository(db)
    professional = await professional_repo.get_by_id(professional_id)
    if professional is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profissional não encontrado neste tenant.")

    service = CapacityService(ProfessionalAvailabilityRepository(db), CapacityRepository(db))
    result = await service.get_utilization(professional_id, date_from, date_to)

    return UtilizationResponse(
        professional_id=professional_id,
        professional_name=professional.full_name,
        available_minutes=result.available_minutes,
        booked_minutes=result.booked_minutes,
        utilization_rate=round(result.utilization_rate, 4),
        no_show_rate=round(result.no_show_rate, 4),
        total_appointments=result.total_appointments,
        status_breakdown=result.status_breakdown,
    )
