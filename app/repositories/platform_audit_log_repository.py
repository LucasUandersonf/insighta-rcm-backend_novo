"""
app/repositories/platform_audit_log_repository.py — ver DECISÃO completa
em app/sql/029_platform_users.sql.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_audit_log import PlatformAuditLog


class PlatformAuditLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, *, platform_user_id: uuid.UUID, action: str) -> PlatformAuditLog:
        entry = PlatformAuditLog(platform_user_id=platform_user_id, action=action)
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_recent(self, *, limit: int = 50) -> list[PlatformAuditLog]:
        stmt = select(PlatformAuditLog).order_by(PlatformAuditLog.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
