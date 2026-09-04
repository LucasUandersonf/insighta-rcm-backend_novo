"""
app/api/v1/endpoints/billing.py

Este endpoint demonstra o encadeamento completo em ação:
  DbSession (já tenant-aware, injetada via deps.py)
    -> BillingRepository
    -> BillingService
    -> BillingResponse (schema de saída, nunca o ORM cru)

Nenhuma linha aqui menciona tenant_id explicitamente nas queries — a
segurança de isolamento é uma propriedade do banco (RLS), não desta
camada. Esta camada só decide RBAC (quem pode fazer o quê).
"""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.billing_repository import BillingRepository
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.guia_repository import GuiaRepository
from app.schemas.billing import BillingCreateRequest, BillingResponse, BillingSettleRequest
from app.schemas.pagination import PaginatedResponse
from app.services.billing_service import BillingService

router = APIRouter(prefix="/billing", tags=["billing"])


def _build_service(db: DbSession) -> BillingService:
    return BillingService(BillingRepository(db), AppointmentRepository(db), ContractItemRepository(db), GuiaRepository(db))


@router.post("", response_model=BillingResponse, status_code=201)
async def create_billing(
    payload: BillingCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role("financeiro", "admin", "owner")),
) -> BillingResponse:
    return await _build_service(db).create_billing(current_user.tenant_id, payload)


@router.get("/high-risk", response_model=PaginatedResponse[BillingResponse])
async def list_high_risk_billing(
    db: DbSession,
    limit: int = 20,
    offset: int = 0,
    current_user: CurrentUser = Depends(require_role("financeiro", "admin", "owner")),
) -> PaginatedResponse[BillingResponse]:
    """
    Alimenta o "Painel" (fila operacional de faturamentos de alto risco).
    Repare: mesmo que o `current_user` seja da Clínica A, é fisicamente
    impossível este endpoint retornar um billing da Clínica B — o RLS
    filtra antes mesmo da linha chegar ao Python.

    Resposta paginada — {items, total, limit, offset} — mesmo envelope
    de contracts/active, denial-appeals e patients (ver
    app/schemas/pagination.py); `limit` é limitado a 200 para não
    permitir uma varredura completa da tabela em uma única chamada.
    """
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    return await _build_service(db).list_high_risk_paginated(limit=limit, offset=offset)


@router.post("/{billing_id}/settle", response_model=BillingResponse)
async def settle_billing(
    billing_id: UUID,
    payload: BillingSettleRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role("financeiro", "admin", "owner")),
) -> BillingResponse:
    """Módulo de Taxas, Custos e Repasses: registra o valor que a
    operadora efetivamente repassou na liquidação do lote."""
    return await _build_service(db).settle_billing(billing_id, payload)
