"""
app/api/v1/endpoints/glosas.py

Glosa REAL — Fase 3 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Mesmo RBAC de guias.py/
lotes.py/faturas.py/billing.py.
"""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.billing_repository import BillingRepository
from app.repositories.glosa_repository import GlosaRepository
from app.schemas.glosa import GlosaCreateRequest, GlosaReconciliationResponse, GlosaResponse
from app.schemas.pagination import PaginatedResponse
from app.services.glosa_service import GlosaService

router = APIRouter(prefix="/glosas", tags=["glosas"])

_CAN_WRITE = ("financeiro", "admin", "owner")
_CAN_READ = (*_CAN_WRITE, "auditor")


def _build_service(db: DbSession) -> GlosaService:
    return GlosaService(GlosaRepository(db), BillingRepository(db))


def _default_period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    # Mesmo default de 7 dias de app/api/v1/endpoints/analytics.py —
    # duplicado aqui de propósito (domínio próprio, não vale acoplar
    # dois endpoints por causa de um helper de formatação de 3 linhas).
    resolved_end = date_to or date.today()
    resolved_start = date_from or (resolved_end - timedelta(days=6))
    if resolved_start > resolved_end:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date_from deve ser <= date_to.")
    return resolved_start, resolved_end


@router.post("", response_model=GlosaResponse, status_code=201)
async def create_glosa(
    payload: GlosaCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> GlosaResponse:
    """Registra o FATO de uma glosa real recebida da operadora — não
    abre um recurso automaticamente (isso continua sendo uma decisão
    humana separada, ver POST /denial-appeals)."""
    return await _build_service(db).create_glosa(current_user.tenant_id, payload)


@router.get("", response_model=PaginatedResponse[GlosaResponse])
async def list_glosas(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[GlosaResponse]:
    return await _build_service(db).list_glosas_paginated(limit=limit, offset=offset)


# Precisa vir ANTES de GET /{glosa_id} — senão "reconciliacao" seria
# interpretado como um UUID de glosa e devolveria 422 (mesmo cuidado de
# GET /users/me em users.py).
@router.get("/reconciliacao", response_model=GlosaReconciliationResponse)
async def get_reconciliation(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    date_from: date | None = None,
    date_to: date | None = None,
) -> GlosaReconciliationResponse:
    """Previsto (denial_risk_engine.py) x Realizado (glosas registradas)
    — a métrica que prova se o motor de risco de glosa funciona de
    verdade, não só "parece funcionar" (ver DECISÃO completa em
    app/schemas/glosa.py::GlosaReconciliationResponse)."""
    start, end = _default_period(date_from, date_to)
    return await _build_service(db).get_reconciliation(start, end)


@router.get("/{glosa_id}", response_model=GlosaResponse)
async def get_glosa(
    glosa_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> GlosaResponse:
    return await _build_service(db).get_glosa(glosa_id)
