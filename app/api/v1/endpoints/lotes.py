"""
app/api/v1/endpoints/lotes.py

Lote — Fase 2 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Mesmo RBAC de guias.py/
billing.py: dado financeiro sensível, fora do alcance de 'atendimento'.
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.guia_repository import GuiaRepository
from app.repositories.lote_repository import LoteRepository
from app.schemas.guia import GuiaResponse
from app.schemas.lote import LoteCreateRequest, LoteResponse
from app.schemas.pagination import PaginatedResponse
from app.services.lote_service import LoteService

router = APIRouter(prefix="/lotes", tags=["lotes"])

_CAN_WRITE = ("financeiro", "admin", "owner")
_CAN_READ = (*_CAN_WRITE, "auditor")


def _build_service(db: DbSession) -> LoteService:
    return LoteService(LoteRepository(db), GuiaRepository(db))


@router.post("", response_model=LoteResponse, status_code=201)
async def create_lote(
    payload: LoteCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> LoteResponse:
    return await _build_service(db).create_lote(current_user.tenant_id, payload)


@router.get("", response_model=PaginatedResponse[LoteResponse])
async def list_lotes(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[LoteResponse]:
    return await _build_service(db).list_lotes_paginated(limit=limit, offset=offset)


@router.get("/{lote_id}", response_model=LoteResponse)
async def get_lote(
    lote_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> LoteResponse:
    return await _build_service(db).get_lote(lote_id)


@router.get("/{lote_id}/guias", response_model=list[GuiaResponse])
async def list_guias_in_lote(
    lote_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> list[GuiaResponse]:
    return await _build_service(db).list_guias_in_lote(lote_id)


@router.post("/{lote_id}/guias/{guia_id}", response_model=GuiaResponse)
async def add_guia_to_lote(
    lote_id: uuid.UUID,
    guia_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> GuiaResponse:
    """Equivale a "Atribuir ao Lote" de um ERP real. Valida mesmo
    convênio + mesmo tipo entre guia e lote (ver DECISÃO em
    app/sql/016_lotes_faturas.sql)."""
    return await _build_service(db).add_guia(lote_id, guia_id)


@router.delete("/{lote_id}/guias/{guia_id}", response_model=GuiaResponse)
async def remove_guia_from_lote(
    lote_id: uuid.UUID,
    guia_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> GuiaResponse:
    return await _build_service(db).remove_guia(lote_id, guia_id)


@router.post("/{lote_id}/fechar", response_model=LoteResponse)
async def fechar_lote(
    lote_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> LoteResponse:
    """Equivale ao "Bloquear" de um ERP real — trava o lote para edição,
    pronto para virar fatura."""
    return await _build_service(db).fechar_lote(lote_id)
