import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketing_spend import MarketingSpend


class MarketingSpendRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, marketing_spend_id: uuid.UUID) -> MarketingSpend | None:
        stmt = select(MarketingSpend).where(MarketingSpend.id == marketing_spend_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[MarketingSpend], int]:
        items_stmt = (
            select(MarketingSpend)
            .order_by(MarketingSpend.spend_date.desc(), MarketingSpend.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(select(func.count()).select_from(MarketingSpend))).scalar_one()
        return items, total

    async def add(self, marketing_spend: MarketingSpend) -> MarketingSpend:
        self.session.add(marketing_spend)
        await self.session.flush()
        return marketing_spend

    async def delete(self, marketing_spend: MarketingSpend) -> None:
        await self.session.delete(marketing_spend)
        await self.session.flush()
