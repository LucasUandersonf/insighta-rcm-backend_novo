import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fatura import Fatura


class FaturaRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, fatura_id: uuid.UUID) -> Fatura | None:
        stmt = select(Fatura).where(Fatura.id == fatura_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[Fatura], int]:
        items_stmt = select(Fatura).order_by(Fatura.created_at.desc()).limit(limit).offset(offset)
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(select(func.count()).select_from(Fatura))).scalar_one()
        return items, total

    async def add(self, fatura: Fatura) -> Fatura:
        self.session.add(fatura)
        await self.session.flush()
        return fatura

    async def save(self, fatura: Fatura) -> Fatura:
        await self.session.flush()
        return fatura
