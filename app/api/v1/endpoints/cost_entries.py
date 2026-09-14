"""
app/api/v1/endpoints/cost_entries.py

Plano Diretor Insighta, épico F3.1 ("Módulo de custos e margem real")
— ver DECISÃO completa em app/sql/039_cost_entries.sql. Mesmo RBAC de
contracts.py/lotes.py: dado financeiro sensível, fora do alcance de
'atendimento'.
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.cost_entry_repository import CostEntryRepository
from app.schemas.cost_entry import CostEntryCreateRequest, CostEntryResponse
from app.schemas.pagination import PaginatedResponse
from app.services.cost_entry_service import CostEntryService

router = APIRouter(prefix="/cost-entries", tags=["cost-entries"])

_CAN_WRITE = ("owner", "admin", "financeiro")
_CAN_READ = (*_CAN_WRITE, "auditor")


def _build_service(db: DbSession) -> CostEntryService:
    return CostEntryService(CostEntryRepository(db))


@router.post("", response_model=CostEntryResponse, status_code=201)
async def create_cost_entry(
    payload: CostEntryCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> CostEntryResponse:
    return await _build_service(db).create_cost_entry(current_user.tenant_id, uuid.UUID(current_user.id), payload)


@router.get("", response_model=PaginatedResponse[CostEntryResponse])
async def list_cost_entries(
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> PaginatedResponse[CostEntryResponse]:
    return await _build_service(db).list_cost_entries(limit=limit, offset=offset)


@router.delete("/{cost_entry_id}", status_code=204)
async def delete_cost_entry(
    cost_entry_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> None:
    await _build_service(db).delete_cost_entry(cost_entry_id)
