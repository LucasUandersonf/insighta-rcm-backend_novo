"""
app/api/v1/endpoints/insight_outcomes.py

Plano Diretor Insighta — épicos F1.2 (ciclo fechado de insight) + F1.3
(atribuição/workflow). Ver DECISÃO completa em
app/services/insight_outcome_service.py.
"""
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, CurrentUserDep, DbSession, require_role
from app.repositories.insight_outcome_repository import InsightOutcomeRepository
from app.schemas.insight_outcome import (
    InsightOutcomeCreateRequest,
    InsightOutcomeResponse,
    InsightOutcomesRealizedSummary,
    InsightOutcomeUpdateRequest,
)
from app.schemas.pagination import PaginatedResponse
from app.services.insight_outcome_service import InsightOutcomeService

router = APIRouter(prefix="/insight-outcomes", tags=["insight-outcomes"])

# Criar/listar a fila geral é ação de quem GERENCIA (mesmo critério de
# lotes.py/_CAN_WRITE) — decidir o que entra no ciclo fechado é decisão
# de gestor. "/mine" abaixo é a exceção: QUALQUER papel autenticado
# pode ver/mexer no que foi atribuído A ELE (ver DECISÃO no service).
_CAN_WRITE = ("owner", "admin", "financeiro")
_CAN_READ = (*_CAN_WRITE, "auditor")


def _build_service(db: DbSession) -> InsightOutcomeService:
    return InsightOutcomeService(InsightOutcomeRepository(db))


@router.post("", response_model=InsightOutcomeResponse, status_code=201)
async def create_insight_outcome(
    payload: InsightOutcomeCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> InsightOutcomeResponse:
    return await _build_service(db).create_outcome(current_user.tenant_id, uuid.UUID(current_user.id), payload)


@router.get("", response_model=PaginatedResponse[InsightOutcomeResponse])
async def list_insight_outcomes(
    db: DbSession,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> PaginatedResponse[InsightOutcomeResponse]:
    return await _build_service(db).list_outcomes(limit=limit, offset=offset, status_filter=status_filter)


@router.get("/mine", response_model=PaginatedResponse[InsightOutcomeResponse])
async def list_my_insight_outcomes(
    db: DbSession,
    current_user: CurrentUserDep,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[InsightOutcomeResponse]:
    """Épico F1.3: "Visão 'Meus pendentes' por usuário, além da fila
    geral do gestor" — QUALQUER papel autenticado (inclusive
    'atendimento'/'auditor') vê o que foi atribuído A ELE, mesmo sem
    _CAN_READ da fila geral. Sempre filtrado pelo próprio id — nunca
    aceita `assigned_to` de outra pessoa via query param."""
    return await _build_service(db).list_outcomes(
        limit=limit, offset=offset, status_filter=status_filter, assigned_to=uuid.UUID(current_user.id)
    )


@router.get("/realized-summary", response_model=InsightOutcomesRealizedSummary)
async def get_insight_outcomes_realized_summary(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> InsightOutcomesRealizedSummary:
    """Épico F1.2: painel "Insights que valeram a pena" — generaliza o
    card de valor protegido pelo motor anti-glosa pra qualquer
    categoria que passou pelo ciclo fechado (resolvido + reavaliado)."""
    return await _build_service(db).get_realized_summary()


@router.get("/{outcome_id}", response_model=InsightOutcomeResponse)
async def get_insight_outcome(
    outcome_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> InsightOutcomeResponse:
    return await _build_service(db).get_outcome(outcome_id)


@router.patch("/{outcome_id}", response_model=InsightOutcomeResponse)
async def update_insight_outcome(
    outcome_id: uuid.UUID,
    payload: InsightOutcomeUpdateRequest,
    db: DbSession,
    current_user: CurrentUserDep,
) -> InsightOutcomeResponse:
    """Sem require_role aqui DE PROPÓSITO: quem gerencia (owner/admin/
    financeiro) pode alterar qualquer campo de qualquer item; quem NÃO
    gerencia só pode mexer no PRÓPRIO item atribuído, e só em status/
    resolution_note — o service (não a rota) decide isso, porque a
    permissão depende de DADO (é o dono do item?), não só de papel."""
    return await _build_service(db).update_outcome(
        actor_user_id=uuid.UUID(current_user.id), actor_role=current_user.role, outcome_id=outcome_id, data=payload
    )
