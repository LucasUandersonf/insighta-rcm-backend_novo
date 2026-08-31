"""
app/repositories/denial_appeal_repository.py

Repositório recebe a `session` já tenant-aware (RLS garante isolamento —
ver DECISÃO padrão em billing_repository.py).
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.denial_appeal import DenialAppeal


class DenialAppealRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, appeal: DenialAppeal) -> DenialAppeal:
        self.session.add(appeal)
        await self.session.flush()
        return appeal

    async def save(self, appeal: DenialAppeal) -> DenialAppeal:
        await self.session.flush()
        return appeal

    async def get_by_id(self, appeal_id: uuid.UUID) -> DenialAppeal | None:
        stmt = select(DenialAppeal).where(DenialAppeal.id == appeal_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, *, status_filter: str | None = None) -> list[DenialAppeal]:
        stmt = select(DenialAppeal).order_by(DenialAppeal.deadline_at.asc())
        if status_filter:
            stmt = stmt.where(DenialAppeal.status == status_filter)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(
        self, *, limit: int, offset: int, status_filter: str | None = None
    ) -> tuple[list[DenialAppeal], int]:
        filters = []
        if status_filter:
            filters.append(DenialAppeal.status == status_filter)

        count_stmt = select(func.count()).select_from(DenialAppeal).where(*filters)
        total = (await self.session.execute(count_stmt)).scalar_one()

        items_stmt = (
            select(DenialAppeal)
            .where(*filters)
            .order_by(DenialAppeal.deadline_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(items_stmt)
        return list(result.scalars().all()), total

    async def list_due_within(self, *, as_of: date, horizon_days: int) -> list[DenialAppeal]:
        """Recursos ainda ABERTOS/PROTOCOLADOS cujo prazo vence dentro da
        janela — alimenta o alerta de prazo (Painel de Insights e o KPI
        `appeals_due_soon_count` de executive-summary). Prazo JÁ VENCIDO
        (deadline_at < as_of) também entra — é o caso mais urgente de
        todos, não deveria "sumir" da lista por já ter passado."""

        horizon_date = as_of + timedelta(days=horizon_days)
        stmt = (
            select(DenialAppeal)
            .where(
                DenialAppeal.status.in_(("aberto", "protocolado")),
                DenialAppeal.deadline_at <= horizon_date,
            )
            .order_by(DenialAppeal.deadline_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_due_within(self, *, as_of: date, horizon_days: int) -> int:
        """Mesma janela de list_due_within, mas COUNT no banco em vez de
        materializar as linhas — é o número que alimenta o KPI de
        executive-summary, chamado com muito mais frequência que a tela
        de detalhe da lista."""

        horizon_date = as_of + timedelta(days=horizon_days)
        stmt = select(func.count()).select_from(DenialAppeal).where(
            DenialAppeal.status.in_(("aberto", "protocolado")),
            DenialAppeal.deadline_at <= horizon_date,
        )
        return int((await self.session.execute(stmt)).scalar_one())
