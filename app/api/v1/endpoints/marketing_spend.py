"""
app/api/v1/endpoints/marketing_spend.py

Achado do Dossiê Insighta RCM — Onda 2 do Plano de Ação ("fecha o
pipeline de escrita de core.marketing_spend"). Mesmo RBAC de
cost_entries.py: dado financeiro sensível, fora do alcance de
'atendimento'.
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.marketing_spend_repository import MarketingSpendRepository
from app.schemas.marketing_spend import MarketingSpendCreateRequest, MarketingSpendResponse
from app.schemas.pagination import PaginatedResponse
from app.services.marketing_spend_service import MarketingSpendService

router = APIRouter(prefix="/marketing-spend", tags=["marketing-spend"])

_CAN_WRITE = ("owner", "admin", "financeiro")
_CAN_READ = (*_CAN_WRITE, "auditor")


def _build_service(db: DbSession) -> MarketingSpendService:
    return MarketingSpendService(MarketingSpendRepository(db))


@router.post("", response_model=MarketingSpendResponse, status_code=201)
async def create_marketing_spend(
    payload: MarketingSpendCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> MarketingSpendResponse:
    return await _build_service(db).create_marketing_spend(current_user.tenant_id, payload)


@router.get("", response_model=PaginatedResponse[MarketingSpendResponse])
async def list_marketing_spend(
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> PaginatedResponse[MarketingSpendResponse]:
    return await _build_service(db).list_marketing_spend(limit=limit, offset=offset)


@router.delete("/{marketing_spend_id}", status_code=204)
async def delete_marketing_spend(
    marketing_spend_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> None:
    await _build_service(db).delete_marketing_spend(marketing_spend_id)
