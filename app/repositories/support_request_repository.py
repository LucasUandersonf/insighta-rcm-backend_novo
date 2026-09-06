"""Mesmo padrão de report_recipient_repository.py: `session` já
tenant-aware, RLS garante isolamento."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.support_request import SupportRequest


class SupportRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, request: SupportRequest) -> SupportRequest:
        self.session.add(request)
        await self.session.flush()
        return request

    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[SupportRequest]:
        stmt = (
            select(SupportRequest)
            .order_by(SupportRequest.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
