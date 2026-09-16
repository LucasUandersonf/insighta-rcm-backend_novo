import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_entry import CostEntry


class CostEntryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, cost_entry_id: uuid.UUID) -> CostEntry | None:
        stmt = select(CostEntry).where(CostEntry.id == cost_entry_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_period(self, *, date_from: date, date_to: date) -> list[CostEntry]:
        """`period_month` é sempre dia 1 do mês (ver DECISÃO no schema)
        — um custo lançado pro mês entra se ESSE MÊS tem qualquer
        sobreposição com [date_from, date_to], mesmo critério que
        "período" já significa no resto do produto (inclusivo nas duas
        pontas)."""
        month_start_floor = date_from.replace(day=1)
        stmt = (
            select(CostEntry)
            .where(CostEntry.period_month >= month_start_floor, CostEntry.period_month <= date_to)
            .order_by(CostEntry.period_month.desc(), CostEntry.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[CostEntry], int]:
        items_stmt = (
            select(CostEntry).order_by(CostEntry.period_month.desc(), CostEntry.created_at.desc()).limit(limit).offset(offset)
        )
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(select(func.count()).select_from(CostEntry))).scalar_one()
        return items, total

    async def add(self, cost_entry: CostEntry) -> CostEntry:
        self.session.add(cost_entry)
        await self.session.flush()
        return cost_entry

    async def delete(self, cost_entry: CostEntry) -> None:
        await self.session.delete(cost_entry)
        await self.session.flush()
