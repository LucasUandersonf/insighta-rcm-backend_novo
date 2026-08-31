"""tenants é a ÚNICA tabela sem RLS (ver app/models/tenant.py) — por
isso, diferente dos outros repositórios, aqui o filtro por id É
necessário e explícito: nada no banco impede este repositório de ler
outro tenant se o service passar o id errado. A garantia de "só o
próprio tenant" vem do service SEMPRE chamar get_by_id(current_user.tenant_id),
nunca de um id vindo de input externo."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, tenant_id: uuid.UUID) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, tenant: Tenant) -> Tenant:
        await self.session.flush()
        return tenant
