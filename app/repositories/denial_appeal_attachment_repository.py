import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.denial_appeal import DenialAppealAttachment


class DenialAppealAttachmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, attachment: DenialAppealAttachment) -> DenialAppealAttachment:
        self.session.add(attachment)
        await self.session.flush()
        return attachment

    async def list_by_appeal(self, appeal_id: uuid.UUID) -> list[DenialAppealAttachment]:
        stmt = (
            select(DenialAppealAttachment)
            .where(DenialAppealAttachment.appeal_id == appeal_id)
            .order_by(DenialAppealAttachment.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
