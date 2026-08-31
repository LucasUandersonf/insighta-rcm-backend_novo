"""
app/api/v1/endpoints/audit_log.py

Leitura do trilho de auditoria (`core.audit_log`) — o model já existia
(app/models/audit_log.py) desde antes desta mudança, gravado por outras
partes do sistema, mas nunca exposto por nenhum endpoint. Somente
leitura (não existe POST/PATCH/DELETE — quem grava é o próprio código
que audita uma ação, não um cliente HTTP). RBAC inclui 'auditor' (dado
de auditoria é literalmente a razão de existir dessa role), diferente do
padrão owner/admin usado em report_recipients.py.

Endpoint puramente de leitura sobre uma única tabela — reaproveita o
padrão "endpoint fala direto com o repositório, sem service" já usado em
reports.py, em vez de um service que só delonga a chamada.
"""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.audit_log import AuditLogResponse
from app.schemas.pagination import PaginatedResponse

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

_CAN_READ = ("owner", "admin", "auditor")


@router.get("", response_model=PaginatedResponse[AuditLogResponse])
async def list_audit_log(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    entity_type: str | None = Query(None),
    action: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
) -> PaginatedResponse[AuditLogResponse]:
    """Resposta: `{items: AuditLogResponse[], total, limit, offset}` —
    `total` é a contagem de registros que casam com os filtros (não o
    total da tabela inteira), para o cliente montar paginação de verdade."""
    repo = AuditLogRepository(db)
    items, total = await repo.list_paginated(
        UUID(current_user.tenant_id),
        limit=limit,
        offset=offset,
        entity_type=entity_type,
        action=action,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        items=[AuditLogResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
