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

    async def add(self, tenant: Tenant) -> Tenant:
        """Cadastro público (self-signup) — ver app/services/auth_service.py.
        Único INSERT deste repositório: fora do RLS por natureza (mesma
        razão de get_by_id não filtrar por current_tenant_id), então quem
        chama isto precisa ser um fluxo explicitamente autorizado a criar
        tenant (hoje, só o registro público)."""
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def save(self, tenant: Tenant) -> Tenant:
        await self.session.flush()
        return tenant
