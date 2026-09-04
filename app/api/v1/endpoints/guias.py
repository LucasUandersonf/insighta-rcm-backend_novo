"""
app/api/v1/endpoints/guias.py

Guia (TISS) — Fase 1 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Mesmo RBAC de billing.py/
contracts.py: dado financeiro sensível, fora do alcance de 'atendimento'.
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.guia_repository import GuiaRepository
from app.schemas.guia import GuiaCreateRequest, GuiaResponse, GuiaUpdateRequest
from app.schemas.pagination import PaginatedResponse
from app.services.guia_service import GuiaService

router = APIRouter(prefix="/guias", tags=["guias"])

_CAN_WRITE = ("financeiro", "admin", "owner")
_CAN_READ = (*_CAN_WRITE, "auditor")


def _build_service(db: DbSession) -> GuiaService:
    return GuiaService(GuiaRepository(db))


@router.post("", response_model=GuiaResponse, status_code=201)
async def create_guia(
    payload: GuiaCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> GuiaResponse:
    return await _build_service(db).create_guia(current_user.tenant_id, payload)


@router.get("", response_model=PaginatedResponse[GuiaResponse])
async def list_guias(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[GuiaResponse]:
    return await _build_service(db).list_guias_paginated(limit=limit, offset=offset)


@router.get("/{guia_id}", response_model=GuiaResponse)
async def get_guia(
    guia_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> GuiaResponse:
    return await _build_service(db).get_guia(guia_id)


@router.patch("/{guia_id}", response_model=GuiaResponse)
async def update_guia(
    guia_id: uuid.UUID,
    payload: GuiaUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> GuiaResponse:
    return await _build_service(db).update_guia(guia_id, payload)
