import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lote import Lote


class LoteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, lote_id: uuid.UUID) -> Lote | None:
        stmt = select(Lote).where(Lote.id == lote_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, lote_ids: list[uuid.UUID]) -> list[Lote]:
        """Usado por FaturaService.create_from_lotes — RLS já garante que
        só vêm lotes do tenant atual, então um id de outro tenant
        simplesmente não aparece no resultado (mesmo efeito de "não
        encontrado" que get_by_id tem em todo o resto do código)."""
        stmt = select(Lote).where(Lote.id.in_(lote_ids))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[Lote], int]:
        items_stmt = select(Lote).order_by(Lote.created_at.desc()).limit(limit).offset(offset)
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(select(func.count()).select_from(Lote))).scalar_one()
        return items, total

    async def stale_open_lotes_summary(self, as_of: datetime, stale_after_days: int) -> tuple[int, int | None]:
        """
        Peça que faltava depois do Achado 12 da Auditoria de Templates e
        Insights ("O que resta em aberto"): core.lotes já tem status/
        closed_at modelados desde a Fase 2 (ver app/models/lote.py), mas
        nenhum insight consumia esse dado — um lote com status='aberto'
        que nunca fecha trava as guias dentro dele de virarem fatura, e
        sem este alerta ninguém percebe até alguém perguntar "cadê aquele
        lote" (mesmo espírito de _appeals_due_soon_insight, mas sem o
        prazo LEGAL que recurso de glosa tem).

        Devolve (quantidade de lotes com status='aberto' e created_at
        anterior ao corte, idade em dias do MAIS ANTIGO deles) — o
        segundo valor é None quando a contagem é 0 (nada pra reportar).
        """
        cutoff = as_of - timedelta(days=stale_after_days)
        stmt = select(func.count(), func.min(Lote.created_at)).where(Lote.status == "aberto", Lote.created_at < cutoff)
        count, oldest_created_at = (await self.session.execute(stmt)).one()
        oldest_age_days = (as_of - oldest_created_at).days if oldest_created_at is not None else None
        return int(count), oldest_age_days

    async def add(self, lote: Lote) -> Lote:
        self.session.add(lote)
        await self.session.flush()
        return lote

    async def save(self, lote: Lote) -> Lote:
        await self.session.flush()
        return lote
