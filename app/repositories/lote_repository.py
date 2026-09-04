import uuid

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

    async def add(self, lote: Lote) -> Lote:
        self.session.add(lote)
        await self.session.flush()
        return lote

    async def save(self, lote: Lote) -> Lote:
        await self.session.flush()
        return lote
