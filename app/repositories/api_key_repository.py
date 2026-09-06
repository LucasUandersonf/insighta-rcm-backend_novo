"""Mesmo padrão de patient_repository.py: sem WHERE tenant_id manual — o
RLS, sob a sessão tenant-aware injetada pelo endpoint, já garante isso."""
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey


@dataclass
class ApiKeyCandidate:
    id: uuid.UUID
    tenant_id: uuid.UUID
    key_hash: str
    revoked_at: datetime | None


class ApiKeyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_candidates_by_prefix(self, key_prefix: str) -> list[ApiKeyCandidate]:
        """
        Resolve candidatos de API key SEM contexto de tenant já setado —
        ver DECISÃO completa em app/sql/024_api_key_resolver.sql (mesmo
        problema de "ovo e galinha" do login: quem chama ainda não sabe o
        tenant, é justamente o que está tentando descobrir). Chama-se
        aqui com `self.session` vindo de `get_db_no_tenant()` — a função
        SQL por trás (`core.resolve_api_key_candidates`) é SECURITY
        DEFINER e bypassa RLS por dentro, então funciona mesmo sem
        `app.current_tenant` setado nesta sessão.
        """
        stmt = text("SELECT id, tenant_id, key_hash, revoked_at FROM core.resolve_api_key_candidates(:prefix)")
        result = await self.session.execute(stmt, {"prefix": key_prefix})
        return [ApiKeyCandidate(id=row.id, tenant_id=row.tenant_id, key_hash=row.key_hash, revoked_at=row.revoked_at) for row in result.all()]

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
