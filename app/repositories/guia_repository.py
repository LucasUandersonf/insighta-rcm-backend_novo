import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guia import Guia


class GuiaRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, guia_id: uuid.UUID) -> Guia | None:
        stmt = select(Guia).where(Guia.id == guia_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[Guia], int]:
        items_stmt = select(Guia).order_by(Guia.created_at.desc()).limit(limit).offset(offset)
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(select(func.count()).select_from(Guia))).scalar_one()
        return items, total

    async def add(self, guia: Guia) -> Guia:
        self.session.add(guia)
        await self.session.flush()
        return guia

    async def save(self, guia: Guia) -> Guia:
        await self.session.flush()
        return guia
