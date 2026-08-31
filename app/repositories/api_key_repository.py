"""Mesmo padrão de patient_repository.py: sem WHERE tenant_id manual — o
RLS, sob a sessão tenant-aware injetada pelo endpoint, já garante isso."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey


class ApiKeyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[ApiKey]:
        stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, api_key_id: uuid.UUID) -> ApiKey | None:
        stmt = select(ApiKey).where(ApiKey.id == api_key_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, api_key: ApiKey) -> ApiKey:
        self.session.add(api_key)
        await self.session.flush()
        return api_key

    async def save(self, api_key: ApiKey) -> ApiKey:
        await self.session.flush()
        return api_key
