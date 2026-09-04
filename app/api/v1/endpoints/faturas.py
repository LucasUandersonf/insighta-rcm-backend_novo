"""
app/api/v1/endpoints/faturas.py

Fatura — Fase 2 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Mesmo RBAC de lotes.py/
guias.py/billing.py.
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.fatura_repository import FaturaRepository
from app.repositories.lote_repository import LoteRepository
from app.schemas.fatura import FaturaCreateRequest, FaturaResponse, FaturaSettleRequest
from app.schemas.pagination import PaginatedResponse
from app.services.fatura_service import FaturaService

router = APIRouter(prefix="/faturas", tags=["faturas"])

_CAN_WRITE = ("financeiro", "admin", "owner")
_CAN_READ = (*_CAN_WRITE, "auditor")


def _build_service(db: DbSession) -> FaturaService:
    return FaturaService(FaturaRepository(db), LoteRepository(db))


@router.post("", response_model=FaturaResponse, status_code=201)
async def create_fatura(
    payload: FaturaCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> FaturaResponse:
    """Gera uma fatura a partir de um ou mais lotes já FECHADOS do mesmo
    convênio — equivale a "Gera Arquivo - TISS"/"Faturamento Emitido" de
    um ERP real (a emissão do XML em si fica para uma fase futura, ver
    PLANO_ADEQUACAO_TISS.md)."""
    return await _build_service(db).create_from_lotes(current_user.tenant_id, payload)


@router.get("", response_model=PaginatedResponse[FaturaResponse])
async def list_faturas(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[FaturaResponse]:
    return await _build_service(db).list_faturas_paginated(limit=limit, offset=offset)


@router.get("/{fatura_id}", response_model=FaturaResponse)
async def get_fatura(
    fatura_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> FaturaResponse:
    return await _build_service(db).get_fatura(fatura_id)


@router.post("/{fatura_id}/baixar", response_model=FaturaResponse)
async def baixar_fatura(
    fatura_id: uuid.UUID,
    payload: FaturaSettleRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> FaturaResponse:
    """Baixa da fatura — equivale a "Baixa Fatura" de um ERP real:
    registra o valor efetivamente recebido da operadora."""
    return await _build_service(db).settle_fatura(fatura_id, payload)
