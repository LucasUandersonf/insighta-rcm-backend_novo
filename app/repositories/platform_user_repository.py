"""
app/repositories/platform_user_repository.py — ver DECISÃO completa em
app/sql/029_platform_users.sql. Sessão sempre SEM tenant
(get_db_no_tenant) — não há RLS nesta tabela para bypassar.
"""
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_user import PlatformUser


class PlatformUserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_email(self, email: str) -> PlatformUser | None:
        stmt = select(PlatformUser).where(PlatformUser.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, platform_user_id: uuid.UUID) -> PlatformUser | None:
        stmt = select(PlatformUser).where(PlatformUser.id == platform_user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def touch_last_login(self, user: PlatformUser, *, now: datetime) -> None:
        user.last_login_at = now
        await self.session.flush()
