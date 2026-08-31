"""
app/repositories/audit_log_repository.py

Repositório recebe a `session` já tenant-aware (RLS garante isolamento —
ver DECISÃO padrão em billing_repository.py). `core.audit_log` já existe
como model (app/models/audit_log.py) desde antes desta mudança, mas
nunca tinha repositório/service/endpoint — só era escrito, nunca lido
pela API.
"""
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_paginated(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        entity_type: str | None = None,
        action: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[AuditLog], int]:
        """Retorna (itens da página, total de registros que casam com os
        filtros — não só o total da tabela). `tenant_id` é redundante com
        o RLS da sessão injetada pelo endpoint (que já filtra pelo tenant
        corrente), mas é aceito explicitamente para deixar o contrato do
        método autoexplicativo, no mesmo espírito de
        ReportRecipientRepository.list_for_report_type."""
        filters = [AuditLog.tenant_id == tenant_id]
        if entity_type:
            filters.append(AuditLog.entity_type == entity_type)
        if action:
            filters.append(AuditLog.action == action)
        if date_from:
            filters.append(func.date(AuditLog.created_at) >= date_from)
        if date_to:
            filters.append(func.date(AuditLog.created_at) <= date_to)

        count_stmt = select(func.count()).select_from(AuditLog).where(*filters)
        total = (await self.session.execute(count_stmt)).scalar_one()

        items_stmt = (
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(items_stmt)
        return list(result.scalars().all()), total
